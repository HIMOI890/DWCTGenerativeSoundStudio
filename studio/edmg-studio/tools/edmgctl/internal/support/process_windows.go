package support

import (
	"fmt"
	"os/exec"
	"syscall"
)

func configureProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x00000008 | 0x00000200}
}

func stopProcessTree(pid int, force bool) error {
	if pid <= 0 {
		return nil
	}
	cmd := exec.Command("taskkill", "/PID", fmt.Sprintf("%d", pid), "/T", "/F")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Run(); err != nil {
		alive, checkErr := processAlive(pid)
		if checkErr != nil {
			return fmt.Errorf("re-check launched process %d: %w", pid, checkErr)
		}
		if alive {
			return err
		}
	}
	return nil
}
