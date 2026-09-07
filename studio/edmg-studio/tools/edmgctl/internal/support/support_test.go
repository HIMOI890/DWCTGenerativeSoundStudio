package support

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestDefaultStoragePaths(t *testing.T) {
	home := filepath.Join("D:\\", "EDMG-Studio")
	paths := DefaultStoragePaths(home)

	if paths.DataDir != filepath.Join(home, "data") {
		t.Fatalf("expected data dir under studio home, got %s", paths.DataDir)
	}
	if paths.OllamaModelsDir != filepath.Join(home, "models", "ollama") {
		t.Fatalf("expected Ollama models dir under models root, got %s", paths.OllamaModelsDir)
	}
	if paths.ElectronUserData != filepath.Join(home, "electron") {
		t.Fatalf("expected electron user data under studio home, got %s", paths.ElectronUserData)
	}
}

func TestResolveStoragePathsWithOverrides(t *testing.T) {
	home := filepath.Join("D:\\", "EDMG-Studio")
	cfg := BootstrapConfig{
		StudioHome: home,
		StorageSettings: StorageOverrides{
			CacheRoot: filepath.Join(home, "cache-alt"),
			LogsDir:   filepath.Join(home, "logs-alt"),
		},
	}

	paths := ResolveStoragePaths(home, cfg)
	if paths.CacheRoot != filepath.Join(home, "cache-alt") {
		t.Fatalf("expected cache override, got %s", paths.CacheRoot)
	}
	if paths.LogsDir != filepath.Join(home, "logs-alt") {
		t.Fatalf("expected logs override, got %s", paths.LogsDir)
	}
	if paths.ModelsDir != filepath.Join(home, "models") {
		t.Fatalf("expected default models dir, got %s", paths.ModelsDir)
	}
}

func TestNewArtifactStatusWithHash(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "artifact.bin")
	content := []byte("edmg-artifact\n")
	if err := os.WriteFile(target, content, 0o644); err != nil {
		t.Fatalf("write artifact: %v", err)
	}

	status := newArtifactStatus("fixture", target, true)
	if !status.Exists {
		t.Fatalf("expected artifact to exist")
	}
	if status.Size != int64(len(content)) {
		t.Fatalf("expected size %d, got %d", len(content), status.Size)
	}
	expected := fmt.Sprintf("%x", sha256.Sum256(content))
	if status.SHA256 != expected {
		t.Fatalf("expected sha256 %s, got %s", expected, status.SHA256)
	}
	if status.Modified == "" {
		t.Fatalf("expected modified timestamp")
	}
}

func TestBuildManagedBackendEnvIncludesCoreKeys(t *testing.T) {
	home := filepath.Join(t.TempDir(), "studio-home")
	cfg := BootstrapConfig{
		AISettings: AISettings{
			Mode:        "local",
			Provider:    "ollama",
			OllamaURL:   "http://127.0.0.1:11434",
			OllamaModel: "qwen3:8b",
		},
	}
	paths := DefaultStoragePaths(home)
	env := BuildManagedBackendEnv(cfg, paths, "127.0.0.1", 5999, filepath.Join(home, "ffmpeg.exe"))
	joined := strings.Join(env, "\n")

	for _, expected := range []string{
		"EDMG_STUDIO_HOME=" + paths.StudioHome,
		"EDMG_STUDIO_DATA_DIR=" + paths.DataDir,
		"EDMG_STUDIO_MODELS_DIR=" + paths.ModelsDir,
		"EDMG_STUDIO_CACHE_DIR=" + paths.CacheRoot,
		"EDMG_STUDIO_LOGS_DIR=" + paths.LogsDir,
		"EDMG_STUDIO_EXTERNAL_DIR=" + paths.ExternalDir,
		"EDMG_STUDIO_BACKEND_HOST=127.0.0.1",
		"EDMG_STUDIO_BACKEND_PORT=5999",
		"EDMG_AI_PROVIDER=ollama",
		"EDMG_FFMPEG_PATH=" + filepath.Join(home, "ffmpeg.exe"),
	} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("expected env to contain %q", expected)
		}
	}
}

func TestBuildManagedBackendEnvOverridesInheritedHuggingFaceCaches(t *testing.T) {
	managedKeys := []string{
		"HF_HOME",
		"HF_HUB_CACHE",
		"HF_XET_CACHE",
		"HF_ASSETS_CACHE",
		"HUGGINGFACE_HUB_CACHE",
		"HUGGINGFACE_ASSETS_CACHE",
		"TRANSFORMERS_CACHE",
	}
	for _, key := range managedKeys {
		t.Setenv(key, `G:\stale-cache\`+key)
	}

	paths := DefaultStoragePaths(filepath.Join(t.TempDir(), "selected-studio-home"))
	flattened := BuildManagedBackendEnv(BootstrapConfig{}, paths, "127.0.0.1", 7863, "")
	env := make(map[string]string, len(flattened))
	for _, raw := range flattened {
		key, value, found := strings.Cut(raw, "=")
		if found {
			env[key] = value
		}
	}

	huggingFaceRoot := filepath.Join(paths.CacheRoot, "huggingface")
	expected := map[string]string{
		"HF_HOME":                  huggingFaceRoot,
		"HF_HUB_CACHE":             filepath.Join(huggingFaceRoot, "hub"),
		"HF_XET_CACHE":             filepath.Join(huggingFaceRoot, "xet"),
		"HF_ASSETS_CACHE":          filepath.Join(huggingFaceRoot, "assets"),
		"HUGGINGFACE_HUB_CACHE":    filepath.Join(huggingFaceRoot, "hub"),
		"HUGGINGFACE_ASSETS_CACHE": filepath.Join(huggingFaceRoot, "assets"),
		"TRANSFORMERS_CACHE":       filepath.Join(paths.CacheRoot, "transformers"),
	}
	for key, value := range expected {
		if env[key] != value {
			t.Fatalf("expected %s=%q, got %q", key, value, env[key])
		}
	}
}

func TestCompareArtifactSetsMatch(t *testing.T) {
	expected := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}
	current := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}

	issues := compareArtifactSets(expected, current)
	if len(issues) != 0 {
		t.Fatalf("expected no issues, got %v", issues)
	}
}

func TestCompareArtifactSetsMismatch(t *testing.T) {
	expected := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 10, SHA256: "abc"},
	}
	current := []ArtifactStatus{
		{Label: "backend bundle", Path: `D:\out\backend.exe`, Exists: true, Size: 11, SHA256: "def"},
		{Label: "installer", Path: `D:\out\installer.exe`, Exists: true},
	}

	issues := compareArtifactSets(expected, current)
	if len(issues) < 2 {
		t.Fatalf("expected multiple issues, got %v", issues)
	}
}

func TestSupportBundleFileName(t *testing.T) {
	name := supportBundleFileName(time.Date(2026, 3, 21, 15, 4, 5, 0, time.UTC))
	if name != "edmg-support-20260321-150405.zip" {
		t.Fatalf("unexpected bundle filename %s", name)
	}
}

func TestReleaseProofPointersIncludeCoreProofs(t *testing.T) {
	proofs := releaseProofPointers(`D:\DWCTGenerativeSoundStudio`)
	joined := make([]string, 0, len(proofs))
	for _, proof := range proofs {
		joined = append(joined, proof.Command)
	}
	commands := strings.Join(joined, "\n")
	for _, expected := range []string{
		"pnpm run validate:release",
		"pnpm run validate:packaged-customer-flow",
		"pnpm run validate:packaged-upgrade-proof",
		"pnpm run validate:packaged-zero-state-setup",
	} {
		if !strings.Contains(commands, expected) {
			t.Fatalf("expected release proofs to contain %q", expected)
		}
	}
}
func TestStartManagedBackendPersistsStateDuringReadiness(t *testing.T) {
	repoRoot, _, cleanup := setupManagedBackendTestFixture(t)
	defer cleanup()

	t.Setenv("EDMG_FAKE_HEALTH_DELAY_MS", "1500")
	statePath, err := SupervisorStatePath()
	if err != nil {
		t.Fatalf("resolve supervisor state path: %v", err)
	}

	type result struct {
		status SupervisorStatus
		err    error
	}
	done := make(chan result, 1)
	go func() {
		status, err := StartManagedBackend(repoRoot, "127.0.0.1", 0, 5*time.Second)
		done <- result{status: status, err: err}
	}()

	time.Sleep(300 * time.Millisecond)
	data, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatalf("expected supervisor state during readiness, read err=%v", err)
	}
	var pending SupervisorState
	if err := json.Unmarshal(data, &pending); err != nil {
		t.Fatalf("unmarshal pending supervisor state: %v", err)
	}
	if pending.PID == 0 || pending.AttemptID == "" {
		t.Fatalf("expected pending supervisor state to capture pid and attempt, got %#v", pending)
	}

	select {
	case outcome := <-done:
		if outcome.err != nil {
			t.Fatalf("start managed backend: %v", outcome.err)
		}
		if !outcome.status.Known {
			t.Fatalf("expected supervisor state to stay persisted after readiness")
		}
		if !outcome.status.Healthy {
			t.Fatalf("expected managed backend to be healthy")
		}
		if !fileExists(statePath) {
			t.Fatalf("expected supervisor state file at %s", statePath)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for managed backend readiness")
	}
}

func TestStartManagedBackendReadinessFailureKillsOnlyManagedChildAndPreservesUnrelatedState(t *testing.T) {
	repoRoot, configRoot, cleanup := setupManagedBackendTestFixture(t)
	defer cleanup()

	childPIDPath := filepath.Join(configRoot, "child.pid")
	t.Setenv("EDMG_FAKE_HEALTH_DELAY_MS", "5000")
	t.Setenv("EDMG_FAKE_SPAWN_CHILD", "1")
	t.Setenv("EDMG_FAKE_CHILD_PID_FILE", childPIDPath)

	statePath, err := SupervisorStatePath()
	if err != nil {
		t.Fatalf("resolve supervisor state path: %v", err)
	}

	type result struct {
		status SupervisorStatus
		err    error
	}
	done := make(chan result, 1)
	go func() {
		status, err := StartManagedBackend(repoRoot, "127.0.0.1", 0, 700*time.Millisecond)
		done <- result{status: status, err: err}
	}()

	var childPID int
	waitForCondition(t, 5*time.Second, func() bool {
		data, err := os.ReadFile(childPIDPath)
		if err != nil {
			return false
		}
		pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
		if err != nil {
			return false
		}
		childPID = pid
		return childPID > 0
	})

	unrelated := &SupervisorState{
		Name:        "other-backend",
		PID:         424242,
		Host:        "127.0.0.1",
		Port:        9797,
		BaseURL:     "http://127.0.0.1:9797",
		CommandPath: filepath.Join(repoRoot, "other.exe"),
		StudioHome:  filepath.Join(configRoot, "other-home"),
		StartedAt:   time.Now().UTC().Format(time.RFC3339),
	}
	if err := WriteSupervisorState(unrelated); err != nil {
		t.Fatalf("write unrelated supervisor state: %v", err)
	}

	select {
	case outcome := <-done:
		if outcome.err == nil {
			t.Fatal("expected readiness failure")
		}
		if outcome.status.Healthy {
			t.Fatalf("expected failed startup to remain unhealthy")
		}
		if !outcome.status.Known {
			t.Fatalf("expected failed startup to report persisted supervisor state")
		}
		if outcome.status.State == nil {
			t.Fatalf("expected failed startup to report launched process details")
		}
		if outcome.status.ProcessAlive {
			t.Fatalf("expected launched child to be terminated after readiness failure")
		}
		if !strings.Contains(outcome.err.Error(), outcome.status.State.LogPath) {
			t.Fatalf("expected actionable log path in error, got %v", outcome.err)
		}
		waitForCondition(t, 5*time.Second, func() bool {
			alive, _ := processAlive(outcome.status.State.PID)
			return !alive
		})
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for failed managed backend startup")
	}

	waitForCondition(t, 5*time.Second, func() bool {
		alive, _ := processAlive(childPID)
		return alive
	})

	data, err := os.ReadFile(statePath)
	if err != nil {
		t.Fatalf("read supervisor state: %v", err)
	}
	var preserved SupervisorState
	if err := json.Unmarshal(data, &preserved); err != nil {
		t.Fatalf("unmarshal preserved state: %v", err)
	}
	if !sameSupervisorState(&preserved, unrelated) {
		t.Fatalf("expected unrelated supervisor state to remain, got %#v", preserved)
	}
	_ = stopProcessTree(childPID, true)
}

func TestStartManagedBackendStartupExitReturnsActionableError(t *testing.T) {
	repoRoot, _, cleanup := setupManagedBackendTestFixture(t)
	defer cleanup()

	t.Setenv("EDMG_FAKE_EXIT_CODE", "17")

	status, err := StartManagedBackend(repoRoot, "127.0.0.1", 0, 2*time.Second)
	if err == nil {
		t.Fatal("expected startup failure")
	}
	if status.State == nil {
		t.Fatalf("expected startup failure to report launched process")
	}
	if status.ProcessAlive {
		t.Fatalf("expected exited process to be marked stopped")
	}
	if !strings.Contains(err.Error(), "exited before becoming healthy") {
		t.Fatalf("expected actionable exit detail, got %v", err)
	}
	if !strings.Contains(err.Error(), status.State.LogPath) {
		t.Fatalf("expected actionable log path in error, got %v", err)
	}
}

func setupManagedBackendTestFixture(t *testing.T) (repoRoot string, configRoot string, cleanup func()) {
	t.Helper()

	repoRoot = t.TempDir()
	configRoot = filepath.Join(repoRoot, "config-root")
	if runtime.GOOS == "windows" {
		t.Setenv("APPDATA", configRoot)
		t.Setenv("LOCALAPPDATA", configRoot)
	} else {
		t.Setenv("XDG_CONFIG_HOME", configRoot)
		t.Setenv("HOME", configRoot)
	}

	bootstrapPath, err := BootstrapConfigPath()
	if err != nil {
		t.Fatalf("resolve bootstrap path: %v", err)
	}
	bootstrapDir := filepath.Dir(bootstrapPath)
	studioHome := filepath.Join(configRoot, "studio-home")
	if err := os.MkdirAll(bootstrapDir, 0o755); err != nil {
		t.Fatalf("create bootstrap dir: %v", err)
	}
	bootstrap := BootstrapConfig{StudioHome: studioHome}
	bootstrapData, err := json.MarshalIndent(bootstrap, "", "  ")
	if err != nil {
		t.Fatalf("marshal bootstrap config: %v", err)
	}
	if err := os.WriteFile(bootstrapPath, append(bootstrapData, '\n'), 0o644); err != nil {
		t.Fatalf("write bootstrap config: %v", err)
	}

	studioDir := filepath.Join(repoRoot, StudioRelDir)
	backendDir := filepath.Join(studioDir, BackendRelDir)
	if err := os.MkdirAll(filepath.Join(repoRoot, ".git"), 0o755); err != nil {
		t.Fatalf("create git dir: %v", err)
	}
	if err := os.MkdirAll(backendDir, 0o755); err != nil {
		t.Fatalf("create backend dir: %v", err)
	}
	packageData := []byte("{\"name\":\"edmg-studio\",\"version\":\"0.0.0-test\"}\n")
	if err := os.WriteFile(filepath.Join(studioDir, PackageJSON), packageData, 0o644); err != nil {
		t.Fatalf("write package metadata: %v", err)
	}

	backendPath := buildFakeManagedBackend(t, backendDir)
	statePath, err := SupervisorStatePath()
	if err != nil {
		t.Fatalf("resolve supervisor state path: %v", err)
	}
	return repoRoot, configRoot, func() {
		state, err := ReadSupervisorState()
		if err == nil && state != nil && samePath(state.CommandPath, backendPath) {
			_, _ = StopManagedBackend()
		} else {
			_ = os.Remove(statePath)
		}
		_ = os.Remove(backendPath)
	}
}

func buildFakeManagedBackend(t *testing.T, backendDir string) string {
	t.Helper()

	ext := ""
	if runtime.GOOS == "windows" {
		ext = ".exe"
	}
	backendPath := filepath.Join(backendDir, "edmg-studio-backend"+ext)
	sourcePath := filepath.Join(backendDir, "fake-managed-backend.go")
	source := `package main

	import (
		"fmt"
		"net"
		"net/http"
		"os"
		"os/exec"
		"strconv"
		"time"
	)

	func main() {
		if len(os.Args) > 1 && os.Args[1] == "__child" {
			if pidPath := os.Getenv("EDMG_FAKE_CHILD_PID_FILE"); pidPath != "" {
				_ = os.WriteFile(pidPath, []byte(strconv.Itoa(os.Getpid())), 0o644)
			}
			time.Sleep(2 * time.Minute)
			return
		}

		host := "127.0.0.1"
		port := "7863"
		for i := 1; i < len(os.Args); i++ {
			switch os.Args[i] {
			case "--host":
				if i+1 < len(os.Args) {
					host = os.Args[i+1]
					i++
				}
			case "--port":
				if i+1 < len(os.Args) {
					port = os.Args[i+1]
					i++
				}
			}
		}

		if os.Getenv("EDMG_FAKE_SPAWN_CHILD") == "1" {
			child := exec.Command(os.Args[0], "__child")
			child.Stdout = os.Stdout
			child.Stderr = os.Stderr
			child.Env = os.Environ()
			if err := child.Start(); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(3)
			}
		}

		if delayMs, _ := strconv.Atoi(os.Getenv("EDMG_FAKE_HEALTH_DELAY_MS")); delayMs > 0 {
			time.Sleep(time.Duration(delayMs) * time.Millisecond)
		}
		if exitCode, _ := strconv.Atoi(os.Getenv("EDMG_FAKE_EXIT_CODE")); exitCode > 0 {
			fmt.Fprintln(os.Stderr, "simulated startup failure")
			os.Exit(exitCode)
		}

		mux := http.NewServeMux()
		mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte("{\"ok\":true}"))
		})

		listener, err := net.Listen("tcp", net.JoinHostPort(host, port))
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(4)
		}
		if err := http.Serve(listener, mux); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(5)
		}
	}
	`
	if err := os.WriteFile(sourcePath, []byte(source), 0o644); err != nil {
		t.Fatalf("write fake backend source: %v", err)
	}
	cmd := exec.Command("go", "build", "-o", backendPath, sourcePath)
	cmd.Env = os.Environ()
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("build fake backend: %v\n%s", err, output)
	}
	return backendPath
}

func waitForCondition(t *testing.T, timeout time.Duration, predicate func() bool) {
	t.Helper()

	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if predicate() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("condition not satisfied before timeout")
}
