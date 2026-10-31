package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFirmwareHashRechecksReplacementContentsAndPermissions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "firmware")
	if err := os.WriteFile(path, []byte("first"), 0600); err != nil {
		t.Fatal(err)
	}
	cache := firmwareHashCache{}
	first := cache.read(path)
	if first == "" || cache.read(path) != first {
		t.Fatal("initial hash")
	}
	info, _ := os.Stat(path)
	time.Sleep(time.Millisecond)
	if err := os.WriteFile(path, []byte("other"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, info.ModTime(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	if changed := cache.read(path); changed == "" || changed == first || changed != hashPath(path) {
		t.Fatal("same-size edit with restored mtime was missed")
	}
	if err := os.Chmod(path, 0666); err != nil {
		t.Fatal(err)
	}
	if cache.read(path) != "" {
		t.Fatal("unsafe permissions reused a cached hash")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(t.TempDir(), "absent"), path); err != nil {
		t.Fatal(err)
	}
	if cache.read(path) != "" {
		t.Fatal("symlink reused a cached hash")
	}
}
