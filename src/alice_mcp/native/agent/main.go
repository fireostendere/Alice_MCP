package main

import (
	"errors"
	"flag"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"strconv"
	"syscall"
	"time"
)

type config struct {
	StationID              string     `json:"station_id"`
	Model                  string     `json:"model"`
	Listen                 string     `json:"listen"`
	Certificate            string     `json:"certificate_file"`
	PrivateKey             string     `json:"private_key_file"`
	CredentialFile         string     `json:"token_file"`
	FirmwareSHA256         string     `json:"firmware_sha256"`
	TTSClient              string     `json:"tts_client"`
	ASRClient              string     `json:"asr_client,omitempty"`
	AllowedActions         []string   `json:"allowed_actions"`
	PreauthorizeAllActions bool       `json:"preauthorize_all_actions"`
	LocalPCM               *pcmConfig `json:"local_pcm,omitempty"`
}

func hex64(value string) bool {
	if len(value) != 64 {
		return false
	}
	for _, c := range value {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

func trustedFile(path string, secret bool) (*os.File, error) {
	bad := errors.New("Untrusted local file")
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return nil, bad
	}
	for candidate := path; ; candidate = filepath.Dir(candidate) {
		info, err := os.Lstat(candidate)
		if err != nil || info.Mode()&os.ModeSymlink != 0 {
			return nil, bad
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || stat.Uid != 0 && stat.Uid != uint32(os.Geteuid()) ||
			info.Mode().Perm()&0022 != 0 && !(info.IsDir() && info.Mode()&os.ModeSticky != 0) {
			return nil, bad
		}
		if candidate == path && (!info.Mode().IsRegular() || secret && info.Mode().Perm()&0077 != 0) {
			return nil, bad
		}
		if candidate == filepath.Dir(candidate) {
			break
		}
	}
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, bad
	}
	return f, nil
}

func readTrusted(path string, secret bool, limit int64) ([]byte, error) {
	f, err := trustedFile(path, secret)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errors.New("Local file unavailable or oversized")
	}
	return data, nil
}

func loadConfig(path string) (config, []byte, error) {
	bad := errors.New("Invalid private configuration")
	data, err := readTrusted(path, true, 65536)
	var c config
	if err != nil || !decode(data, &c) || c.Model != "station-mini" && c.Model != "station-mini3" ||
		len(c.StationID) < 1 || len(c.StationID) > 32 || !hex64(c.FirmwareSHA256) {
		return config{}, nil, bad
	}
	for i, value := range c.StationID {
		if !(value >= 'a' && value <= 'z' || i > 0 && (value >= '0' && value <= '9' || value == '-')) {
			return config{}, nil, bad
		}
	}
	host, port, err := net.SplitHostPort(c.Listen)
	number, numberErr := strconv.Atoi(port)
	if err != nil || numberErr != nil || number < 1024 || number > 65535 || net.ParseIP(host) == nil {
		return config{}, nil, bad
	}
	for _, item := range []struct {
		path   string
		secret bool
	}{{c.Certificate, false}, {c.PrivateKey, true}, {c.CredentialFile, true}} {
		if _, err := readTrusted(item.path, item.secret, 65536); err != nil {
			return config{}, nil, bad
		}
	}
	seen := map[string]bool{}
	if c.LocalPCM != nil && (c.Model != "station-mini" || !filepath.IsAbs(c.LocalPCM.Client) ||
		!hex64(c.LocalPCM.ALSAConfigSHA256) || !hex64(c.LocalPCM.LibrarySHA256)) {
		return config{}, nil, bad
	}
	for _, action := range c.AllowedActions {
		if seen[action] || action != "speech.say" && action != "speech.listen" ||
			action == "speech.say" && c.LocalPCM == nil && !filepath.IsAbs(c.TTSClient) ||
			action == "speech.listen" && !filepath.IsAbs(c.ASRClient) {
			return config{}, nil, bad
		}
		seen[action] = true
	}
	return c, data, nil
}

func main() {
	configPath := flag.String("config", "", "Owner-only absolute configuration file")
	flag.Parse()
	if flag.NArg() != 0 || os.Geteuid() != 0 || *configPath == "" {
		_, _ = io.WriteString(os.Stderr, "Root and an explicit private configuration are required\n")
		os.Exit(2)
	}
	c, _, err := loadConfig(*configPath)
	if err != nil {
		_, _ = io.WriteString(os.Stderr, "Private configuration unavailable\n")
		os.Exit(2)
	}
	runtime.GOMAXPROCS(1)
	debug.SetMemoryLimit(32 << 20)
	debug.SetGCPercent(50)
	device := &nativeRuntime{configPath: *configPath, stationID: c.StationID, model: c.Model}
	api := &server{stationID: c.StationID, model: c.Model, tokenFile: c.CredentialFile,
		controls: device.controls, probe: device.probe, run: device.say,
		listen: device.listen, status: device.captureStatus, pcm: device.playPCM}
	httpServer := &http.Server{Addr: c.Listen, Handler: api, TLSConfig: tlsSettings(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second,
		WriteTimeout: 40 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 4096,
		ErrorLog: log.New(io.Discard, "", 0)}
	if err := httpServer.ListenAndServeTLS(c.Certificate, c.PrivateKey); err != nil {
		_, _ = io.WriteString(os.Stderr, "Native MCP listener stopped\n")
		os.Exit(1)
	}
}
