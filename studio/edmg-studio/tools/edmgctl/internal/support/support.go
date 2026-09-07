package support

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
)

const (
	AppName         = "EDMG Studio"
	StudioRelDir    = "studio/edmg-studio"
	BootstrapFile   = "bootstrap.json"
	PackageJSON     = "package.json"
	BackendRelDir   = "electron-resources/backend"
	FFmpegRelDir    = "electron-resources/bin"
	BootstrapRelDir = "EDMG Studio"
)

var (
	supportedPythonMin          = [2]int{3, 10}
	supportedPythonMaxExclusive = [2]int{3, 14}
	defaultStorageRelativePaths = map[string]string{
		"dataDir":     "data",
		"modelsDir":   "models",
		"cacheRoot":   "cache",
		"logsDir":     "logs",
		"externalDir": "external",
	}
)

type PackageMeta struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

type ToolStatus struct {
	Name    string `json:"name"`
	Found   bool   `json:"found"`
	Path    string `json:"path,omitempty"`
	Version string `json:"version,omitempty"`
	Note    string `json:"note,omitempty"`
}

type GitStatus struct {
	Available bool     `json:"available"`
	Head      string   `json:"head,omitempty"`
	Branch    string   `json:"branch,omitempty"`
	Clean     bool     `json:"clean"`
	Dirty     []string `json:"dirty,omitempty"`
	Note      string   `json:"note,omitempty"`
}

type StorageOverrides struct {
	DataDir     string `json:"dataDir,omitempty"`
	ModelsDir   string `json:"modelsDir,omitempty"`
	CacheRoot   string `json:"cacheRoot,omitempty"`
	LogsDir     string `json:"logsDir,omitempty"`
	ExternalDir string `json:"externalDir,omitempty"`
}

type AISettings struct {
	Mode                string `json:"mode,omitempty"`
	Provider            string `json:"provider,omitempty"`
	AIBaseURL           string `json:"aiBaseUrl,omitempty"`
	OllamaURL           string `json:"ollamaUrl,omitempty"`
	OllamaModel         string `json:"ollamaModel,omitempty"`
	OpenAICompatBaseURL string `json:"openaiCompatBaseUrl,omitempty"`
	OpenAICompatModel   string `json:"openaiCompatModel,omitempty"`
}

type LastMigration struct {
	OK      *bool  `json:"ok,omitempty"`
	Reason  string `json:"reason,omitempty"`
	Message string `json:"message,omitempty"`
}

type BootstrapConfig struct {
	StudioHome       string           `json:"studioHome,omitempty"`
	StorageSettings  StorageOverrides `json:"storageSettings,omitempty"`
	AISettings       AISettings       `json:"aiSettings,omitempty"`
	PendingMigration any              `json:"pendingMigration,omitempty"`
	LastMigration    *LastMigration   `json:"lastMigration,omitempty"`
}

type StoragePaths struct {
	StudioHome       string `json:"studioHome"`
	DataDir          string `json:"dataDir"`
	ModelsDir        string `json:"modelsDir"`
	CacheRoot        string `json:"cacheRoot"`
	LogsDir          string `json:"logsDir"`
	ExternalDir      string `json:"externalDir"`
	ElectronUserData string `json:"electronUserData"`
	SessionData      string `json:"sessionData"`
	OllamaModelsDir  string `json:"ollamaModelsDir"`
}

type BootstrapReport struct {
	Path              string          `json:"path"`
	Exists            bool            `json:"exists"`
	Config            BootstrapConfig `json:"config"`
	Resolved          StoragePaths    `json:"resolved"`
	OutsideStudioHome []string        `json:"outsideStudioHome,omitempty"`
	PendingMigration  bool            `json:"pendingMigration"`
	LastMigrationOK   *bool           `json:"lastMigrationOk,omitempty"`
}

type ArtifactStatus struct {
	Label    string `json:"label"`
	Path     string `json:"path"`
	Exists   bool   `json:"exists"`
	Size     int64  `json:"size,omitempty"`
	SHA256   string `json:"sha256,omitempty"`
	Modified string `json:"modified,omitempty"`
}

type ReleaseStatus struct {
	StudioDir          string           `json:"studioDir"`
	Package            PackageMeta      `json:"package"`
	WindowsReleaseHost bool             `json:"windowsReleaseHost"`
	BundleManifestPath string           `json:"bundleManifestPath"`
	BundleManifestOK   bool             `json:"bundleManifestOk"`
	BundleSourceHash   string           `json:"bundleSourceHash,omitempty"`
	Artifacts          []ArtifactStatus `json:"artifacts"`
}

type ArtifactManifest struct {
	GeneratedAt string           `json:"generatedAt"`
	StudioDir   string           `json:"studioDir"`
	Package     PackageMeta      `json:"package"`
	Artifacts   []ArtifactStatus `json:"artifacts"`
}

type SupervisorState struct {
	Name        string `json:"name"`
	PID         int    `json:"pid"`
	Host        string `json:"host"`
	Port        int    `json:"port"`
	BaseURL     string `json:"baseUrl"`
	CommandPath string `json:"commandPath"`
	StudioHome  string `json:"studioHome"`
	LogPath     string `json:"logPath,omitempty"`
	StartedAt   string `json:"startedAt"`
	AttemptID   string `json:"attemptId,omitempty"`
}

type SupervisorStatus struct {
	StateFile    string           `json:"stateFile"`
	Known        bool             `json:"known"`
	ProcessAlive bool             `json:"processAlive"`
	Healthy      bool             `json:"healthy"`
	HealthNote   string           `json:"healthNote,omitempty"`
	State        *SupervisorState `json:"state,omitempty"`
}

type ManifestVerification struct {
	ManifestPath string           `json:"manifestPath"`
	Matches      bool             `json:"matches"`
	Expected     ArtifactManifest `json:"expected"`
	Current      []ArtifactStatus `json:"current"`
	Issues       []string         `json:"issues,omitempty"`
}

type ReleaseProofPointer struct {
	Name    string `json:"name"`
	Command string `json:"command"`
	DocPath string `json:"docPath,omitempty"`
	Note    string `json:"note,omitempty"`
}

type SupportBundleEntry struct {
	Name       string `json:"name"`
	SourcePath string `json:"sourcePath,omitempty"`
}

type SupportBundleSummary struct {
	OutputPath    string                `json:"outputPath"`
	GeneratedAt   string                `json:"generatedAt"`
	RepoRoot      string                `json:"repoRoot"`
	StudioDir     string                `json:"studioDir"`
	Package       PackageMeta           `json:"package"`
	Entries       []SupportBundleEntry  `json:"entries"`
	ReleaseProofs []ReleaseProofPointer `json:"releaseProofs"`
}

type DoctorReport struct {
	Platform   string           `json:"platform"`
	RepoRoot   string           `json:"repoRoot"`
	StudioDir  string           `json:"studioDir"`
	Package    PackageMeta      `json:"package"`
	Git        GitStatus        `json:"git"`
	Tools      []ToolStatus     `json:"tools"`
	Bootstrap  BootstrapReport  `json:"bootstrap"`
	Supervisor SupervisorStatus `json:"supervisor"`
	Release    ReleaseStatus    `json:"release"`
	Warnings   []string         `json:"warnings,omitempty"`
}

func FindRepoRoot(start string) (string, error) {
	if start == "" {
		start = "."
	}
	current, err := filepath.Abs(start)
	if err != nil {
		return "", err
	}

	for {
		studioPackage := filepath.Join(current, StudioRelDir, PackageJSON)
		gitDir := filepath.Join(current, ".git")
		if fileExists(studioPackage) && pathExists(gitDir) {
			return current, nil
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}

	return "", fmt.Errorf("could not find repo root from %s", start)
}

func LoadPackageMeta(repoRoot string) (PackageMeta, error) {
	packagePath := filepath.Join(repoRoot, StudioRelDir, PackageJSON)
	var meta PackageMeta
	data, err := os.ReadFile(packagePath)
	if err != nil {
		return meta, err
	}
	if err := json.Unmarshal(data, &meta); err != nil {
		return meta, err
	}
	if meta.Name == "" || meta.Version == "" {
		return meta, fmt.Errorf("package metadata incomplete in %s", packagePath)
	}
	return meta, nil
}

func CollectDoctorReport(repoRoot string) (DoctorReport, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return DoctorReport{}, err
	}
	studioDir := filepath.Join(repoRoot, StudioRelDir)
	pkg, err := LoadPackageMeta(repoRoot)
	if err != nil {
		return DoctorReport{}, err
	}

	bootstrap, err := ReadBootstrapReport()
	if err != nil {
		return DoctorReport{}, err
	}
	supervisor, err := GetSupervisorStatus()
	if err != nil {
		return DoctorReport{}, err
	}
	release, err := CollectReleaseStatus(repoRoot)
	if err != nil {
		return DoctorReport{}, err
	}

	warnings := make([]string, 0, 6)
	if len(bootstrap.OutsideStudioHome) > 0 {
		warnings = append(warnings, fmt.Sprintf("storage roots escape Studio Home: %s", strings.Join(bootstrap.OutsideStudioHome, ", ")))
	}
	if !release.BundleManifestOK {
		warnings = append(warnings, "backend bundle manifest missing or unreadable")
	}
	for _, artifact := range release.Artifacts {
		if !artifact.Exists {
			warnings = append(warnings, fmt.Sprintf("missing artifact: %s", artifact.Label))
		}
	}
	if !release.WindowsReleaseHost {
		warnings = append(warnings, "current host is not Windows; dist:win may not be runnable here")
	}
	if supervisor.Known && !supervisor.ProcessAlive {
		warnings = append(warnings, "supervisor has stale state for a stopped backend process")
	}
	if supervisor.Known && supervisor.ProcessAlive && !supervisor.Healthy {
		warnings = append(warnings, "supervisor-managed backend is running but not healthy")
	}

	return DoctorReport{
		Platform:   runtime.GOOS + "/" + runtime.GOARCH,
		RepoRoot:   repoRoot,
		StudioDir:  studioDir,
		Package:    pkg,
		Git:        CollectGitStatus(repoRoot),
		Tools:      CollectToolStatus(),
		Bootstrap:  bootstrap,
		Supervisor: supervisor,
		Release:    release,
		Warnings:   warnings,
	}, nil
}

func CollectToolStatus() []ToolStatus {
	tools := []ToolStatus{
		versionedTool("git", []commandCandidate{{Name: "git"}}),
		versionedTool("node", []commandCandidate{{Name: "node"}}),
		versionedTool("pnpm", packageManagerCandidates()),
		pythonToolStatus(),
		{
			Name:    "go",
			Found:   true,
			Path:    executableOrBlank(),
			Version: runtime.Version(),
			Note:    "support-plane CLI toolchain",
		},
	}
	return tools
}

func CollectGitStatus(repoRoot string) GitStatus {
	git := resolveCommand([]commandCandidate{{Name: "git"}})
	if git.Path == "" {
		return GitStatus{Clean: true, Note: "git not found"}
	}

	head := strings.TrimSpace(runAndCapture(repoRoot, git.Path, "rev-parse", "--short", "HEAD"))
	branch := strings.TrimSpace(runAndCapture(repoRoot, git.Path, "rev-parse", "--abbrev-ref", "HEAD"))
	statusOutput := strings.TrimSpace(runAndCapture(repoRoot, git.Path, "status", "--porcelain"))

	status := GitStatus{
		Available: true,
		Head:      head,
		Branch:    branch,
		Clean:     statusOutput == "",
	}

	if statusOutput != "" {
		lines := strings.Split(statusOutput, "\n")
		status.Dirty = make([]string, 0, len(lines))
		for _, line := range lines {
			if strings.TrimSpace(line) == "" {
				continue
			}
			status.Dirty = append(status.Dirty, strings.TrimSpace(line))
		}
	}
	return status
}

func CollectReleaseStatus(repoRoot string) (ReleaseStatus, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return ReleaseStatus{}, err
	}
	studioDir := filepath.Join(repoRoot, StudioRelDir)
	pkg, err := LoadPackageMeta(repoRoot)
	if err != nil {
		return ReleaseStatus{}, err
	}

	manifestPath := filepath.Join(studioDir, BackendRelDir, "backend-bundle-manifest.json")
	manifestHash := ""
	manifestOK := false
	if data, err := os.ReadFile(manifestPath); err == nil {
		var payload struct {
			SourceHash string `json:"sourceHash"`
		}
		if json.Unmarshal(data, &payload) == nil {
			manifestOK = true
			manifestHash = payload.SourceHash
		}
	}

	artifacts, err := CollectArtifactInventory(repoRoot, false)
	if err != nil {
		return ReleaseStatus{}, err
	}

	return ReleaseStatus{
		StudioDir:          studioDir,
		Package:            pkg,
		WindowsReleaseHost: runtime.GOOS == "windows",
		BundleManifestPath: manifestPath,
		BundleManifestOK:   manifestOK,
		BundleSourceHash:   manifestHash,
		Artifacts:          artifacts,
	}, nil
}

func CollectArtifactInventory(repoRoot string, includeHashes bool) ([]ArtifactStatus, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return nil, err
	}
	studioDir := filepath.Join(repoRoot, StudioRelDir)
	pkg, err := LoadPackageMeta(repoRoot)
	if err != nil {
		return nil, err
	}

	return []ArtifactStatus{
		newArtifactStatus("backend bundle", firstExisting(filepath.Join(studioDir, BackendRelDir, "edmg-studio-backend.exe"), filepath.Join(studioDir, BackendRelDir, "edmg-studio-backend")), includeHashes),
		newArtifactStatus("bundled ffmpeg", firstExisting(filepath.Join(studioDir, FFmpegRelDir, "ffmpeg.exe"), filepath.Join(studioDir, FFmpegRelDir, "ffmpeg")), includeHashes),
		newArtifactStatus("win-unpacked app", filepath.Join(studioDir, "dist", "win-unpacked", packagedAppName()), includeHashes),
		newArtifactStatus("installer", filepath.Join(studioDir, "dist", fmt.Sprintf("%s Setup %s.exe", pkg.Name, pkg.Version)), includeHashes),
	}, nil
}

func BuildArtifactManifest(repoRoot string, includeHashes bool) (ArtifactManifest, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return ArtifactManifest{}, err
	}
	pkg, err := LoadPackageMeta(repoRoot)
	if err != nil {
		return ArtifactManifest{}, err
	}
	artifacts, err := CollectArtifactInventory(repoRoot, includeHashes)
	if err != nil {
		return ArtifactManifest{}, err
	}
	return ArtifactManifest{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		StudioDir:   filepath.Join(repoRoot, StudioRelDir),
		Package:     pkg,
		Artifacts:   artifacts,
	}, nil
}

func VerifyArtifactManifest(repoRoot, manifestPath string) (ManifestVerification, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return ManifestVerification{}, err
	}
	manifestPath = cleanPath(manifestPath)
	if manifestPath == "" {
		return ManifestVerification{}, errors.New("manifest path is required")
	}

	data, err := os.ReadFile(manifestPath)
	if err != nil {
		return ManifestVerification{}, err
	}
	var expected ArtifactManifest
	if err := json.Unmarshal(data, &expected); err != nil {
		return ManifestVerification{}, err
	}

	current, err := CollectArtifactInventory(repoRoot, true)
	if err != nil {
		return ManifestVerification{}, err
	}
	issues := compareArtifactSets(expected.Artifacts, current)
	return ManifestVerification{
		ManifestPath: manifestPath,
		Matches:      len(issues) == 0,
		Expected:     expected,
		Current:      current,
		Issues:       issues,
	}, nil
}

func ExportSupportBundle(repoRoot, outPath string) (SupportBundleSummary, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	pkg, err := LoadPackageMeta(repoRoot)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	doctor, err := CollectDoctorReport(repoRoot)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	manifest, err := BuildArtifactManifest(repoRoot, true)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	proofs := releaseProofPointers(repoRoot)

	outPath, err = resolveSupportBundlePath(repoRoot, doctor.Bootstrap, outPath)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return SupportBundleSummary{}, err
	}

	bundleFile, err := os.Create(outPath)
	if err != nil {
		return SupportBundleSummary{}, err
	}
	defer bundleFile.Close()

	zipWriter := zip.NewWriter(bundleFile)
	entries := make([]SupportBundleEntry, 0, 8)
	addJSON := func(name string, value any) error {
		data, err := json.MarshalIndent(value, "", "  ")
		if err != nil {
			return err
		}
		if err := writeZipBytes(zipWriter, name, append(data, '\n')); err != nil {
			return err
		}
		entries = append(entries, SupportBundleEntry{Name: name})
		return nil
	}
	addFile := func(name, sourcePath string) error {
		if !fileExists(sourcePath) {
			return nil
		}
		if err := writeZipFile(zipWriter, name, sourcePath); err != nil {
			return err
		}
		entries = append(entries, SupportBundleEntry{Name: name, SourcePath: sourcePath})
		return nil
	}

	if err := addJSON("doctor.json", doctor); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if err := addJSON("bootstrap-report.json", doctor.Bootstrap); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if err := addJSON("supervisor-status.json", doctor.Supervisor); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if err := addJSON("artifact-manifest.json", manifest); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if err := addJSON("release-proofs.json", proofs); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if doctor.Bootstrap.Exists {
		if err := addFile("bootstrap.json", doctor.Bootstrap.Path); err != nil {
			_ = zipWriter.Close()
			return SupportBundleSummary{}, err
		}
	}
	if doctor.Supervisor.Known && doctor.Supervisor.State != nil {
		statePath := doctor.Supervisor.StateFile
		if err := addFile("edmgctl-supervisor.json", statePath); err != nil {
			_ = zipWriter.Close()
			return SupportBundleSummary{}, err
		}
		if doctor.Supervisor.State.LogPath != "" {
			logName := filepath.ToSlash(filepath.Join("logs", filepath.Base(doctor.Supervisor.State.LogPath)))
			if err := addFile(logName, doctor.Supervisor.State.LogPath); err != nil {
				_ = zipWriter.Close()
				return SupportBundleSummary{}, err
			}
		}
	}

	summary := SupportBundleSummary{
		OutputPath:    outPath,
		GeneratedAt:   time.Now().UTC().Format(time.RFC3339),
		RepoRoot:      repoRoot,
		StudioDir:     filepath.Join(repoRoot, StudioRelDir),
		Package:       pkg,
		Entries:       entries,
		ReleaseProofs: proofs,
	}
	readme := buildSupportBundleReadme(summary)
	if err := writeZipBytes(zipWriter, "README.txt", []byte(readme)); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}
	if err := writeZipBytes(zipWriter, "bundle-summary.json", mustJSON(summary)); err != nil {
		_ = zipWriter.Close()
		return SupportBundleSummary{}, err
	}

	if err := zipWriter.Close(); err != nil {
		return SupportBundleSummary{}, err
	}
	return summary, nil
}

func ReadBootstrapReport() (BootstrapReport, error) {
	bootstrapPath, err := BootstrapConfigPath()
	if err != nil {
		return BootstrapReport{}, err
	}
	report := BootstrapReport{
		Path: bootstrapPath,
	}

	if !fileExists(bootstrapPath) {
		report.Resolved = DefaultStoragePaths(filepath.Dir(bootstrapPath))
		return report, nil
	}

	report.Exists = true
	data, err := os.ReadFile(bootstrapPath)
	if err != nil {
		return report, err
	}
	if err := json.Unmarshal(data, &report.Config); err != nil {
		return report, err
	}

	fallbackHome := filepath.Dir(bootstrapPath)
	report.Resolved = ResolveStoragePaths(fallbackHome, report.Config)
	report.PendingMigration = report.Config.PendingMigration != nil
	if report.Config.LastMigration != nil {
		report.LastMigrationOK = report.Config.LastMigration.OK
	}

	for label, candidate := range map[string]string{
		"dataDir":     report.Resolved.DataDir,
		"modelsDir":   report.Resolved.ModelsDir,
		"cacheRoot":   report.Resolved.CacheRoot,
		"logsDir":     report.Resolved.LogsDir,
		"externalDir": report.Resolved.ExternalDir,
	} {
		if !pathWithin(report.Resolved.StudioHome, candidate) {
			report.OutsideStudioHome = append(report.OutsideStudioHome, label)
		}
	}

	return report, nil
}

func BootstrapConfigPath() (string, error) {
	configDir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(configDir, BootstrapRelDir, BootstrapFile), nil
}

func DefaultStoragePaths(studioHome string) StoragePaths {
	studioHome = cleanPath(studioHome)
	if studioHome == "" {
		studioHome = "."
	}
	electronUserData := filepath.Join(studioHome, "electron")
	return StoragePaths{
		StudioHome:       studioHome,
		DataDir:          filepath.Join(studioHome, defaultStorageRelativePaths["dataDir"]),
		ModelsDir:        filepath.Join(studioHome, defaultStorageRelativePaths["modelsDir"]),
		CacheRoot:        filepath.Join(studioHome, defaultStorageRelativePaths["cacheRoot"]),
		LogsDir:          filepath.Join(studioHome, defaultStorageRelativePaths["logsDir"]),
		ExternalDir:      filepath.Join(studioHome, defaultStorageRelativePaths["externalDir"]),
		ElectronUserData: electronUserData,
		SessionData:      filepath.Join(electronUserData, "session"),
		OllamaModelsDir:  filepath.Join(studioHome, defaultStorageRelativePaths["modelsDir"], "ollama"),
	}
}

func ResolveStoragePaths(fallbackHome string, cfg BootstrapConfig) StoragePaths {
	studioHome := cleanPath(cfg.StudioHome)
	if studioHome == "" {
		studioHome = cleanPath(fallbackHome)
	}

	paths := DefaultStoragePaths(studioHome)
	if override := cleanPath(cfg.StorageSettings.DataDir); override != "" {
		paths.DataDir = override
	}
	if override := cleanPath(cfg.StorageSettings.ModelsDir); override != "" {
		paths.ModelsDir = override
	}
	if override := cleanPath(cfg.StorageSettings.CacheRoot); override != "" {
		paths.CacheRoot = override
	}
	if override := cleanPath(cfg.StorageSettings.LogsDir); override != "" {
		paths.LogsDir = override
	}
	if override := cleanPath(cfg.StorageSettings.ExternalDir); override != "" {
		paths.ExternalDir = override
	}
	paths.OllamaModelsDir = filepath.Join(paths.ModelsDir, "ollama")
	return paths
}

func RunReleaseBuild(repoRoot string) error {
	return runPackageManagerScript(repoRoot, "dist:win")
}

func RunReleaseValidate(repoRoot string) error {
	return runPackageManagerScript(repoRoot, "validate:release")
}

func StartManagedBackend(repoRoot, host string, port int, waitTimeout time.Duration) (SupervisorStatus, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return SupervisorStatus{}, err
	}
	if strings.TrimSpace(host) == "" {
		host = "127.0.0.1"
	}
	if port == 0 {
		port, err = allocatePort(host)
		if err != nil {
			return SupervisorStatus{}, err
		}
	}

	statePath, err := SupervisorStatePath()
	if err != nil {
		return SupervisorStatus{}, err
	}
	current, _ := ReadSupervisorState()
	if current != nil {
		alive, _ := processAlive(current.PID)
		if alive {
			return SupervisorStatus{
				StateFile:    statePath,
				Known:        true,
				ProcessAlive: true,
				Healthy:      healthCheck(current.BaseURL, 2*time.Second) == nil,
				State:        current,
				HealthNote:   "managed backend already running",
			}, fmt.Errorf("managed backend already running with pid %d at %s", current.PID, current.BaseURL)
		}
		_ = removeSupervisorStateIfMatching(current)
	}

	bootstrap, err := ReadBootstrapReport()
	if err != nil {
		return SupervisorStatus{}, err
	}
	backendPath, err := packagedBackendPath(repoRoot)
	if err != nil {
		return SupervisorStatus{}, err
	}
	ffmpegPath := packagedFFmpegPath(repoRoot)
	env := BuildManagedBackendEnv(bootstrap.Config, bootstrap.Resolved, host, port, ffmpegPath)
	logDir := filepath.Join(bootstrap.Resolved.LogsDir, "edmgctl")
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return SupervisorStatus{}, err
	}
	logPath := filepath.Join(logDir, "packaged-backend-supervisor.log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return SupervisorStatus{}, err
	}
	defer logFile.Close()
	_, _ = logFile.WriteString(fmt.Sprintf("\n[%s] launching %s serve --host %s --port %d\n", time.Now().UTC().Format(time.RFC3339), backendPath, host, port))

	cmd := exec.Command(backendPath, "serve", "--host", host, "--port", fmt.Sprintf("%d", port))
	cmd.Dir = filepath.Dir(backendPath)
	cmd.Env = env
	devNull, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		return SupervisorStatus{}, err
	}
	defer devNull.Close()
	cmd.Stdin = devNull
	cmd.Stdout = logFile
	cmd.Stderr = logFile

	if runtime.GOOS == "windows" {
		cmd.SysProcAttr = &syscall.SysProcAttr{
			HideWindow:    true,
			CreationFlags: 0x00000008 | 0x00000200,
		}
	} else {
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	}

	if err := cmd.Start(); err != nil {
		return SupervisorStatus{}, err
	}
	waitCh := make(chan error, 1)
	go func() {
		waitCh <- cmd.Wait()
	}()
	startedAt := time.Now().UTC()

	state := &SupervisorState{
		Name:        "packaged-backend",
		PID:         cmd.Process.Pid,
		Host:        host,
		Port:        port,
		BaseURL:     fmt.Sprintf("http://%s:%d", host, port),
		CommandPath: backendPath,
		StudioHome:  bootstrap.Resolved.StudioHome,
		LogPath:     logPath,
		StartedAt:   startedAt.Format(time.RFC3339Nano),
		AttemptID:   fmt.Sprintf("%d-%d", startedAt.UnixNano(), cmd.Process.Pid),
	}
	status := SupervisorStatus{
		StateFile:    statePath,
		Known:        false,
		ProcessAlive: true,
		State:        state,
	}
	readinessErr := error(nil)
	if waitTimeout > 0 {
		readinessErr = waitForManagedBackendReady(waitCh, state.BaseURL, waitTimeout)
	} else {
		select {
		case waitErr := <-waitCh:
			readinessErr = normalizeManagedProcessWaitError(state.PID, waitErr)
			if readinessErr == nil {
				readinessErr = errors.New("managed backend exited before becoming healthy")
			}
		default:
			readinessErr = healthCheck(state.BaseURL, 1500*time.Millisecond)
		}
	}
	status.Healthy = readinessErr == nil
	if readinessErr != nil {
		cleanupErr := cleanupManagedBackendLaunch(cmd, waitCh, state)
		status.ProcessAlive = false
		status.HealthNote = composeManagedLaunchNote(state.LogPath, readinessErr, cleanupErr)
		return status, composeManagedLaunchError(state.LogPath, nil, readinessErr, cleanupErr)
	}
	if err := WriteSupervisorState(state); err != nil {
		primaryErr := fmt.Errorf("failed to record supervisor state for pid %d: %w", state.PID, err)
		cleanupErr := cleanupManagedBackendLaunch(cmd, waitCh, state)
		status.ProcessAlive = false
		status.HealthNote = composeManagedLaunchNote(state.LogPath, primaryErr, cleanupErr)
		return status, composeManagedLaunchError(state.LogPath, nil, primaryErr, cleanupErr)
	}
	status.Known = true
	return status, nil
}

func GetSupervisorStatus() (SupervisorStatus, error) {
	statePath, err := SupervisorStatePath()
	if err != nil {
		return SupervisorStatus{}, err
	}
	state, err := ReadSupervisorState()
	if err != nil {
		return SupervisorStatus{}, err
	}
	if state == nil {
		return SupervisorStatus{StateFile: statePath}, nil
	}
	alive, err := processAlive(state.PID)
	status := SupervisorStatus{
		StateFile:    statePath,
		Known:        true,
		ProcessAlive: alive,
		State:        state,
	}
	if err != nil {
		status.HealthNote = err.Error()
		return status, nil
	}
	healthErr := healthCheck(state.BaseURL, 2*time.Second)
	status.Healthy = healthErr == nil
	if healthErr != nil {
		status.HealthNote = healthErr.Error()
	}
	return status, nil
}

func StopManagedBackend() (SupervisorStatus, error) {
	statePath, err := SupervisorStatePath()
	if err != nil {
		return SupervisorStatus{}, err
	}
	state, err := ReadSupervisorState()
	if err != nil {
		return SupervisorStatus{}, err
	}
	if state == nil {
		return SupervisorStatus{StateFile: statePath}, nil
	}
	cleanupErr := stopManagedProcessTreeAndWait(state.PID, 10*time.Second)
	if err := removeSupervisorStateIfMatching(state); err != nil {
		cleanupErr = errors.Join(cleanupErr, err)
	}
	status := SupervisorStatus{
		StateFile:    statePath,
		Known:        true,
		ProcessAlive: false,
		Healthy:      false,
		State:        state,
	}
	if cleanupErr != nil {
		if alive, err := processAlive(state.PID); err == nil {
			status.ProcessAlive = alive
		}
		status.HealthNote = composeLifecycleNote(nil, cleanupErr)
		return status, cleanupErr
	}
	return status, nil
}

func runPackageManagerScript(repoRoot, script string) error {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return err
	}
	packageManager := resolveCommand(packageManagerCandidates())
	if packageManager.Path == "" {
		return errors.New("pnpm not found in PATH (or via corepack)")
	}
	args := append([]string{}, packageManager.Args...)
	args = append(args, "run", script)
	cmd := exec.Command(packageManager.Path, args...)
	cmd.Dir = filepath.Join(repoRoot, StudioRelDir)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

func BuildManagedBackendEnv(cfg BootstrapConfig, paths StoragePaths, host string, port int, ffmpegPath string) []string {
	envMap := make(map[string]string, 64)
	for _, raw := range os.Environ() {
		key, value, found := strings.Cut(raw, "=")
		if found {
			envMap[key] = value
		}
	}

	managed := map[string]string{
		"EDMG_STUDIO_HOME":               paths.StudioHome,
		"EDMG_STUDIO_DATA_DIR":           paths.DataDir,
		"EDMG_STUDIO_MODELS_DIR":         paths.ModelsDir,
		"EDMG_STUDIO_CACHE_DIR":          paths.CacheRoot,
		"EDMG_STUDIO_LOGS_DIR":           paths.LogsDir,
		"EDMG_STUDIO_EXTERNAL_DIR":       paths.ExternalDir,
		"OLLAMA_MODELS":                  paths.OllamaModelsDir,
		"PIP_CACHE_DIR":                  filepath.Join(paths.CacheRoot, "pip"),
		"XDG_CACHE_HOME":                 filepath.Join(paths.CacheRoot, "xdg"),
		"HF_HOME":                        filepath.Join(paths.CacheRoot, "huggingface"),
		"HF_HUB_CACHE":                   filepath.Join(paths.CacheRoot, "huggingface", "hub"),
		"HF_XET_CACHE":                   filepath.Join(paths.CacheRoot, "huggingface", "xet"),
		"HF_ASSETS_CACHE":                filepath.Join(paths.CacheRoot, "huggingface", "assets"),
		"HUGGINGFACE_HUB_CACHE":          filepath.Join(paths.CacheRoot, "huggingface", "hub"),
		"HUGGINGFACE_ASSETS_CACHE":       filepath.Join(paths.CacheRoot, "huggingface", "assets"),
		"TRANSFORMERS_CACHE":             filepath.Join(paths.CacheRoot, "transformers"),
		"TORCH_HOME":                     filepath.Join(paths.CacheRoot, "torch"),
		"NLTK_DATA":                      filepath.Join(paths.CacheRoot, "nltk_data"),
		"WHISPER_CACHE_DIR":              filepath.Join(paths.CacheRoot, "whisper"),
		"MPLBACKEND":                     "Agg",
		"MPLCONFIGDIR":                   filepath.Join(paths.CacheRoot, "matplotlib"),
		"TMP":                            filepath.Join(paths.CacheRoot, "tmp"),
		"TEMP":                           filepath.Join(paths.CacheRoot, "tmp"),
		"EDMG_STUDIO_BACKEND_HOST":       host,
		"EDMG_STUDIO_BACKEND_PORT":       fmt.Sprintf("%d", port),
		"EDMG_AI_MODE":                   cfg.AISettings.Mode,
		"EDMG_AI_PROVIDER":               cfg.AISettings.Provider,
		"EDMG_AI_BASE_URL":               cfg.AISettings.AIBaseURL,
		"EDMG_AI_OLLAMA_URL":             cfg.AISettings.OllamaURL,
		"EDMG_AI_OLLAMA_MODEL":           cfg.AISettings.OllamaModel,
		"EDMG_AI_OPENAI_COMPAT_BASE_URL": cfg.AISettings.OpenAICompatBaseURL,
		"EDMG_AI_OPENAI_COMPAT_MODEL":    cfg.AISettings.OpenAICompatModel,
	}
	if ffmpegPath != "" {
		managed["EDMG_FFMPEG_PATH"] = ffmpegPath
	}

	for _, value := range managed {
		if strings.TrimSpace(value) != "" && looksLikePath(value) {
			dirPath := value
			if ext := filepath.Ext(value); ext != "" {
				dirPath = filepath.Dir(value)
			}
			_ = os.MkdirAll(dirPath, 0o755)
		}
	}

	for key, value := range managed {
		if strings.TrimSpace(value) == "" {
			continue
		}
		envMap[key] = value
	}

	flattened := make([]string, 0, len(envMap))
	for key, value := range envMap {
		flattened = append(flattened, key+"="+value)
	}
	return flattened
}

func releaseProofPointers(repoRoot string) []ReleaseProofPointer {
	doc := filepath.Join(repoRoot, "RELEASE.md")
	return []ReleaseProofPointer{
		{
			Name:    "full-release-validation",
			Command: "pnpm run validate:release",
			DocPath: doc,
			Note:    "Runs desktop validation, packaged customer flow, and packaged upgrade proof.",
		},
		{
			Name:    "packaged-customer-flow",
			Command: "pnpm run validate:packaged-customer-flow",
			DocPath: doc,
			Note:    "Verifies create, upload, analyze, plan, run, and output paths in the packaged app.",
		},
		{
			Name:    "packaged-upgrade-proof",
			Command: "pnpm run validate:packaged-upgrade-proof",
			DocPath: doc,
			Note:    "Verifies migration from the older C:\\-style layout into Studio-managed roots.",
		},
		{
			Name:    "packaged-zero-state-setup",
			Command: "pnpm run validate:packaged-zero-state-setup",
			DocPath: doc,
			Note:    "Verifies Studio-managed Ollama and portable 7-Zip setup on a fresh root.",
		},
	}
}

func resolveSupportBundlePath(repoRoot string, bootstrap BootstrapReport, outPath string) (string, error) {
	if clean := cleanPath(outPath); clean != "" {
		return clean, nil
	}
	baseDir := filepath.Join(os.TempDir(), "edmgctl", "support")
	if bootstrap.Resolved.LogsDir != "" {
		baseDir = filepath.Join(bootstrap.Resolved.LogsDir, "edmgctl", "support")
	}
	return filepath.Join(baseDir, supportBundleFileName(time.Now().UTC())), nil
}

func supportBundleFileName(ts time.Time) string {
	return fmt.Sprintf("edmg-support-%s.zip", ts.UTC().Format("20060102-150405"))
}

func buildSupportBundleReadme(summary SupportBundleSummary) string {
	var b strings.Builder
	b.WriteString("EDMG Studio support bundle\n")
	b.WriteString("=========================\n\n")
	b.WriteString("This archive was exported by edmgctl.\n\n")
	b.WriteString("Contents:\n")
	for _, entry := range summary.Entries {
		b.WriteString("- ")
		b.WriteString(entry.Name)
		if entry.SourcePath != "" {
			b.WriteString(" <- ")
			b.WriteString(entry.SourcePath)
		}
		b.WriteString("\n")
	}
	b.WriteString("- README.txt\n")
	b.WriteString("- bundle-summary.json\n\n")
	b.WriteString("Recommended follow-up proofs:\n")
	for _, proof := range summary.ReleaseProofs {
		b.WriteString("- ")
		b.WriteString(proof.Command)
		if proof.Note != "" {
			b.WriteString(" : ")
			b.WriteString(proof.Note)
		}
		b.WriteString("\n")
	}
	return b.String()
}

func mustJSON(value any) []byte {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return []byte("{}\n")
	}
	return append(data, '\n')
}

func writeZipBytes(zipWriter *zip.Writer, name string, data []byte) error {
	writer, err := zipWriter.Create(name)
	if err != nil {
		return err
	}
	_, err = writer.Write(data)
	return err
}

func writeZipFile(zipWriter *zip.Writer, name, sourcePath string) error {
	data, err := os.ReadFile(sourcePath)
	if err != nil {
		return err
	}
	return writeZipBytes(zipWriter, name, data)
}

func compareArtifactSets(expected, current []ArtifactStatus) []string {
	issues := make([]string, 0)
	currentByLabel := make(map[string]ArtifactStatus, len(current))
	for _, artifact := range current {
		currentByLabel[artifact.Label] = artifact
	}

	for _, want := range expected {
		got, ok := currentByLabel[want.Label]
		if !ok {
			issues = append(issues, fmt.Sprintf("missing current artifact entry for %s", want.Label))
			continue
		}
		if want.Exists != got.Exists {
			issues = append(issues, fmt.Sprintf("%s existence mismatch: expected %t, got %t", want.Label, want.Exists, got.Exists))
		}
		if want.Exists && !got.Exists {
			continue
		}
		if want.SHA256 != "" && got.SHA256 != "" && !strings.EqualFold(want.SHA256, got.SHA256) {
			issues = append(issues, fmt.Sprintf("%s sha256 mismatch: expected %s, got %s", want.Label, want.SHA256, got.SHA256))
		}
		if want.Size != 0 && got.Size != 0 && want.Size != got.Size {
			issues = append(issues, fmt.Sprintf("%s size mismatch: expected %d, got %d", want.Label, want.Size, got.Size))
		}
		if cleanPath(want.Path) != "" && cleanPath(got.Path) != "" && cleanPath(want.Path) != cleanPath(got.Path) {
			issues = append(issues, fmt.Sprintf("%s path mismatch: expected %s, got %s", want.Label, want.Path, got.Path))
		}
		delete(currentByLabel, want.Label)
	}

	for label := range currentByLabel {
		issues = append(issues, fmt.Sprintf("unexpected current artifact entry for %s", label))
	}
	return issues
}

func newArtifactStatus(label, artifactPath string, includeHashes bool) ArtifactStatus {
	status := ArtifactStatus{
		Label: label,
		Path:  artifactPath,
	}
	info, err := os.Stat(artifactPath)
	if err != nil || info.IsDir() {
		return status
	}
	status.Exists = true
	status.Size = info.Size()
	status.Modified = info.ModTime().UTC().Format(time.RFC3339)
	if includeHashes {
		status.SHA256 = sha256File(artifactPath)
	}
	return status
}

type commandCandidate struct {
	Name string
	Args []string
}

type resolvedCommand struct {
	Path string
	Args []string
}

func versionedTool(name string, candidates []commandCandidate) ToolStatus {
	resolved := resolveCommand(candidates)
	if resolved.Path == "" {
		return ToolStatus{Name: name, Found: false}
	}
	args := append([]string{}, resolved.Args...)
	switch name {
	case "git":
		args = append(args, "--version")
	case "node":
		args = append(args, "--version")
	case "pnpm":
		args = append(args, "--version")
	default:
		args = append(args, "--version")
	}
	return ToolStatus{
		Name:    name,
		Found:   true,
		Path:    resolved.Path,
		Version: strings.TrimSpace(runAndCapture("", resolved.Path, args...)),
	}
}

func pythonToolStatus() ToolStatus {
	candidates := []commandCandidate{{Name: "python"}}
	if runtime.GOOS == "windows" {
		candidates = append(candidates, commandCandidate{Name: "py", Args: []string{"-3"}})
	}
	resolved := resolveCommand(candidates)
	if resolved.Path == "" {
		return ToolStatus{Name: "python", Found: false}
	}
	args := append([]string{}, resolved.Args...)
	args = append(args, "--version")
	version := strings.TrimSpace(runAndCapture("", resolved.Path, args...))
	status := ToolStatus{
		Name:    "python",
		Found:   true,
		Path:    resolved.Path,
		Version: version,
	}
	maj, min := parseMajorMinor(version)
	if maj == 0 && min == 0 {
		status.Note = "could not parse Python version"
		return status
	}
	if maj != 3 || min < supportedPythonMin[1] || min >= supportedPythonMaxExclusive[1] {
		status.Note = fmt.Sprintf("Studio release builds support Python >= %d.%d and < %d.%d", supportedPythonMin[0], supportedPythonMin[1], supportedPythonMaxExclusive[0], supportedPythonMaxExclusive[1])
	}
	return status
}

func resolveCommand(candidates []commandCandidate) resolvedCommand {
	for _, candidate := range candidates {
		if candidate.Name == "" {
			continue
		}
		path, err := exec.LookPath(candidate.Name)
		if err == nil {
			return resolvedCommand{Path: path, Args: candidate.Args}
		}
	}
	return resolvedCommand{}
}

func runAndCapture(dir, name string, args ...string) string {
	cmd := exec.Command(name, args...)
	if dir != "" {
		cmd.Dir = dir
	}
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if err := cmd.Run(); err != nil {
		return ""
	}
	return out.String()
}

func sha256File(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(data)
	return fmt.Sprintf("%x", sum)
}

func packagedBackendPath(repoRoot string) (string, error) {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return "", err
	}
	studioDir := filepath.Join(repoRoot, StudioRelDir)
	path := firstExisting(
		filepath.Join(studioDir, BackendRelDir, "edmg-studio-backend.exe"),
		filepath.Join(studioDir, BackendRelDir, "edmg-studio-backend"),
	)
	if !fileExists(path) {
		return "", fmt.Errorf("packaged backend not found under %s", filepath.Join(studioDir, BackendRelDir))
	}
	return path, nil
}

func packagedFFmpegPath(repoRoot string) string {
	repoRoot, err := FindRepoRoot(repoRoot)
	if err != nil {
		return ""
	}
	studioDir := filepath.Join(repoRoot, StudioRelDir)
	path := firstExisting(
		filepath.Join(studioDir, FFmpegRelDir, "ffmpeg.exe"),
		filepath.Join(studioDir, FFmpegRelDir, "ffmpeg"),
	)
	if !fileExists(path) {
		return ""
	}
	return path
}

func SupervisorStatePath() (string, error) {
	configDir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(configDir, BootstrapRelDir, "edmgctl-supervisor.json"), nil
}

func ReadSupervisorState() (*SupervisorState, error) {
	statePath, err := SupervisorStatePath()
	if err != nil {
		return nil, err
	}
	if !fileExists(statePath) {
		return nil, nil
	}
	data, err := os.ReadFile(statePath)
	if err != nil {
		return nil, err
	}
	var state SupervisorState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, err
	}
	return &state, nil
}

func WriteSupervisorState(state *SupervisorState) error {
	statePath, err := SupervisorStatePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(statePath), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(statePath, append(data, '\n'), 0o644)
}

func waitForHealthURL(baseURL string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		lastErr = healthCheck(baseURL, 2*time.Second)
		if lastErr == nil {
			return nil
		}
		time.Sleep(750 * time.Millisecond)
	}
	if lastErr == nil {
		lastErr = errors.New("backend did not become healthy in time")
	}
	return lastErr
}

func healthCheck(baseURL string, timeout time.Duration) error {
	client := &http.Client{Timeout: timeout}
	resp, err := client.Get(strings.TrimRight(baseURL, "/") + "/health")
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("health returned %s", resp.Status)
	}
	return nil
}

func allocatePort(host string) (int, error) {
	listener, err := net.Listen("tcp", net.JoinHostPort(host, "0"))
	if err != nil {
		return 0, err
	}
	defer listener.Close()
	addr, ok := listener.Addr().(*net.TCPAddr)
	if !ok {
		return 0, errors.New("failed to allocate tcp port")
	}
	return addr.Port, nil
}

func processAlive(pid int) (bool, error) {
	if pid <= 0 {
		return false, nil
	}
	if runtime.GOOS == "windows" {
		cmd := exec.Command("tasklist", "/FI", fmt.Sprintf("PID eq %d", pid), "/FO", "CSV", "/NH")
		var out bytes.Buffer
		cmd.Stdout = &out
		cmd.Stderr = &out
		if err := cmd.Run(); err != nil {
			return false, err
		}
		text := strings.TrimSpace(out.String())
		if text == "" || strings.HasPrefix(text, "INFO:") {
			return false, nil
		}
		return strings.Contains(text, fmt.Sprintf("\"%d\"", pid)), nil
	}
	cmd := exec.Command("kill", "-0", fmt.Sprintf("%d", pid))
	if err := cmd.Run(); err != nil {
		return false, nil
	}
	return true, nil
}

func stopProcessTree(pid int, force bool) error {
	if pid <= 0 {
		return nil
	}
	if runtime.GOOS == "windows" {
		cmd := exec.Command("taskkill", "/PID", fmt.Sprintf("%d", pid), "/T", "/F")
		if err := cmd.Run(); err != nil {
			alive, aliveErr := processAlive(pid)
			if aliveErr == nil && !alive {
				return nil
			}
			if aliveErr != nil {
				return fmt.Errorf("re-check launched process %d: %w", pid, aliveErr)
			}
			return err
		}
		return nil
	}
	signal := syscall.SIGTERM
	if force {
		signal = syscall.SIGKILL
	}
	if err := syscall.Kill(-pid, signal); err != nil {
		if errors.Is(err, syscall.ESRCH) {
			alive, aliveErr := processAlive(pid)
			if aliveErr == nil && !alive {
				return nil
			}
			if aliveErr != nil {
				return fmt.Errorf("re-check launched process %d: %w", pid, aliveErr)
			}
		}
		return err
	}
	return nil
}

func cleanupManagedBackendLaunch(cmd *exec.Cmd, waitCh <-chan error, state *SupervisorState) error {
	var cleanupErrs []error
	if cmd != nil && cmd.Process != nil {
		if err := stopManagedProcessTreeAndWaitForCmd(cmd, waitCh, 10*time.Second); err != nil {
			cleanupErrs = append(cleanupErrs, err)
		}
	}
	if err := removeSupervisorStateIfMatching(state); err != nil {
		cleanupErrs = append(cleanupErrs, err)
	}
	return errors.Join(cleanupErrs...)
}

func stopManagedProcessTreeAndWaitForCmd(cmd *exec.Cmd, waitCh <-chan error, timeout time.Duration) error {
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	if waitCh != nil {
		select {
		case waitErr := <-waitCh:
			return normalizeManagedProcessWaitError(cmd.Process.Pid, waitErr)
		default:
		}
	}

	if err := stopProcessTree(cmd.Process.Pid, false); err != nil {
		return fmt.Errorf("terminate launched process tree %d: %w", cmd.Process.Pid, err)
	}
	if timedOut, err := waitForManagedProcessExit(cmd.Process.Pid, waitCh, timeout); err != nil {
		return err
	} else if timedOut {
		if err := stopProcessTree(cmd.Process.Pid, true); err != nil {
			return fmt.Errorf("force terminate launched process tree %d: %w", cmd.Process.Pid, err)
		}
		if timedOut, err := waitForManagedProcessExit(cmd.Process.Pid, waitCh, timeout); err != nil {
			return err
		} else if timedOut {
			return fmt.Errorf("launched process tree %d did not exit within %s", cmd.Process.Pid, timeout*2)
		}
	}
	return nil
}

func stopManagedProcessTreeAndWait(pid int, timeout time.Duration) error {
	if pid <= 0 {
		return nil
	}
	if err := stopProcessTree(pid, false); err != nil {
		return fmt.Errorf("terminate launched process tree %d: %w", pid, err)
	}
	if timedOut, err := waitForPIDExit(pid, timeout); err != nil {
		return err
	} else if timedOut {
		if err := stopProcessTree(pid, true); err != nil {
			return fmt.Errorf("force terminate launched process tree %d: %w", pid, err)
		}
		if timedOut, err := waitForPIDExit(pid, timeout); err != nil {
			return err
		} else if timedOut {
			return fmt.Errorf("launched process tree %d did not exit within %s", pid, timeout*2)
		}
	}
	return nil
}

func waitForManagedProcessExit(pid int, waitCh <-chan error, timeout time.Duration) (bool, error) {
	if timeout <= 0 {
		return false, normalizeManagedProcessWaitError(pid, <-waitCh)
	}

	select {
	case err := <-waitCh:
		return false, normalizeManagedProcessWaitError(pid, err)
	case <-time.After(timeout):
		alive, err := processAlive(pid)
		if err != nil {
			return false, fmt.Errorf("wait for launched process %d exit: %w", pid, err)
		}
		if alive {
			return true, nil
		}
		return false, nil
	}
}

func waitForPIDExit(pid int, timeout time.Duration) (bool, error) {
	deadline := time.Now().Add(timeout)
	for {
		alive, err := processAlive(pid)
		if err != nil {
			return false, fmt.Errorf("wait for launched process %d exit: %w", pid, err)
		}
		if !alive {
			return false, nil
		}
		if timeout > 0 && time.Now().After(deadline) {
			return true, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func normalizeManagedProcessWaitError(pid int, waitErr error) error {
	if waitErr == nil {
		return nil
	}
	alive, err := processAlive(pid)
	if err == nil && !alive {
		return nil
	}
	if err != nil {
		return fmt.Errorf("wait for launched process %d exit: %w", pid, err)
	}
	return fmt.Errorf("wait for launched process %d exit: %w", pid, waitErr)
}

func waitForManagedBackendReady(waitCh <-chan error, baseURL string, timeout time.Duration) error {
	if timeout <= 0 {
		return nil
	}
	deadline := time.Now().Add(timeout)
	var lastErr error
	for {
		select {
		case waitErr := <-waitCh:
			if waitErr == nil {
				return errors.New("managed backend exited before becoming healthy")
			}
			return fmt.Errorf("managed backend exited before becoming healthy: %w", waitErr)
		default:
		}

		probeTimeout := 2 * time.Second
		if remaining := time.Until(deadline); remaining < probeTimeout {
			probeTimeout = remaining
		}
		if probeTimeout <= 0 {
			break
		}

		lastErr = healthCheck(baseURL, probeTimeout)
		if lastErr == nil {
			return nil
		}
		if time.Now().After(deadline) {
			break
		}
		sleepFor := 250 * time.Millisecond
		if remaining := time.Until(deadline); remaining < sleepFor {
			sleepFor = remaining
		}
		if sleepFor > 0 {
			time.Sleep(sleepFor)
		}
	}
	if lastErr == nil {
		lastErr = errors.New("backend did not become healthy in time")
	}
	return fmt.Errorf("managed backend readiness timed out after %s: %w", timeout.Round(time.Millisecond), lastErr)
}

func removeSupervisorStateIfMatching(match *SupervisorState) error {
	if match == nil {
		return nil
	}
	statePath, err := SupervisorStatePath()
	if err != nil {
		return err
	}
	current, err := ReadSupervisorState()
	if err != nil {
		return err
	}
	if current == nil || !sameSupervisorState(current, match) {
		return nil
	}
	if err := os.Remove(statePath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func sameSupervisorState(current, match *SupervisorState) bool {
	if current == nil || match == nil {
		return false
	}
	if current.AttemptID != "" && match.AttemptID != "" {
		return current.AttemptID == match.AttemptID
	}
	return current.Name == match.Name &&
		current.PID == match.PID &&
		current.Port == match.Port &&
		current.StartedAt == match.StartedAt &&
		strings.EqualFold(strings.TrimSpace(current.BaseURL), strings.TrimSpace(match.BaseURL)) &&
		samePath(current.CommandPath, match.CommandPath) &&
		samePath(current.StudioHome, match.StudioHome)
}

func samePath(left, right string) bool {
	left = cleanPath(left)
	right = cleanPath(right)
	if runtime.GOOS == "windows" {
		return strings.EqualFold(left, right)
	}
	return left == right
}

func composeLifecycleNote(primaryErr, cleanupErr error) string {
	if primaryErr == nil {
		if cleanupErr == nil {
			return ""
		}
		return cleanupErr.Error()
	}
	if cleanupErr == nil {
		return primaryErr.Error()
	}
	return fmt.Sprintf("%s (cleanup: %s)", primaryErr, cleanupErr)
}

func composeLifecycleError(primaryErr, cleanupErr error) error {
	if primaryErr == nil {
		return cleanupErr
	}
	if cleanupErr == nil {
		return primaryErr
	}
	return fmt.Errorf("%w (cleanup: %v)", primaryErr, cleanupErr)
}

func composeManagedLaunchNote(logPath string, primaryErr, cleanupErr error) string {
	note := composeLifecycleNote(primaryErr, cleanupErr)
	if logPath == "" {
		return note
	}
	if note == "" {
		return fmt.Sprintf("inspect log %s", logPath)
	}
	return fmt.Sprintf("%s (inspect log %s)", note, logPath)
}

func composeManagedLaunchError(logPath string, prefix error, primaryErr, cleanupErr error) error {
	combined := primaryErr
	if prefix != nil {
		combined = fmt.Errorf("%w: %v", prefix, primaryErr)
	}
	if cleanupErr != nil {
		combined = composeLifecycleError(combined, cleanupErr)
	}
	if logPath == "" {
		return combined
	}
	return fmt.Errorf("%w (inspect log %s)", combined, logPath)
}

func parseMajorMinor(version string) (int, int) {
	version = strings.TrimSpace(version)
	version = strings.TrimPrefix(version, "Python ")
	version = strings.TrimPrefix(version, "go version go")
	version = strings.TrimPrefix(version, "v")
	var major, minor int
	if _, err := fmt.Sscanf(version, "%d.%d", &major, &minor); err != nil {
		return 0, 0
	}
	return major, minor
}

func executableOrBlank() string {
	path, err := os.Executable()
	if err != nil {
		return ""
	}
	return path
}

func packageManagerCandidates() []commandCandidate {
	return []commandCandidate{
		{Name: pnpmCommandName()},
		{Name: "corepack", Args: []string{"pnpm"}},
	}
}

func pnpmCommandName() string {
	if runtime.GOOS == "windows" {
		return "pnpm.cmd"
	}
	return "pnpm"
}

func packagedAppName() string {
	if runtime.GOOS == "windows" {
		return "EDMG Studio.exe"
	}
	return "EDMG Studio"
}

func firstExisting(candidates ...string) string {
	for _, candidate := range candidates {
		if fileExists(candidate) {
			return candidate
		}
	}
	if len(candidates) == 0 {
		return ""
	}
	return candidates[0]
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func pathExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func cleanPath(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if resolved, err := filepath.Abs(value); err == nil {
		return resolved
	}
	return filepath.Clean(value)
}

func pathWithin(base, target string) bool {
	base = cleanPath(base)
	target = cleanPath(target)
	if base == "" || target == "" {
		return false
	}
	rel, err := filepath.Rel(base, target)
	if err != nil {
		return false
	}
	if rel == "." {
		return true
	}
	return !strings.HasPrefix(rel, "..") && rel != ".."
}

func looksLikePath(value string) bool {
	if strings.TrimSpace(value) == "" {
		return false
	}
	if strings.Contains(value, "://") {
		return false
	}
	if strings.Contains(value, "\n") {
		return false
	}
	return strings.ContainsAny(value, `\/:`)
}
