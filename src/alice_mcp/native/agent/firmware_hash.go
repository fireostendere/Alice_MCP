package main

import (
	"sync"
	"syscall"
)

// Firmware is large; rehash on any inode metadata change, never cache policy or state.
type firmwareHashCache struct {
	mu     sync.Mutex
	stamp  syscall.Stat_t
	digest string
}

func (c *firmwareHashCache) read(path string) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	f, err := trustedFile(path, false)
	if err != nil {
		return ""
	}
	defer f.Close()
	var before, after syscall.Stat_t
	if syscall.Fstat(int(f.Fd()), &before) != nil {
		return ""
	}
	before.Atim = syscall.Timespec{}
	if c.digest != "" && c.stamp == before {
		return c.digest
	}
	digest, err := hashFile(f, 64<<20)
	if err != nil || syscall.Fstat(int(f.Fd()), &after) != nil {
		return ""
	}
	after.Atim = syscall.Timespec{}
	if before != after {
		return ""
	}
	c.stamp, c.digest = before, digest
	return digest
}
