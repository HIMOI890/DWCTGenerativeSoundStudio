using System.Collections.Concurrent;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using EdmgStudio.WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.Windows.Storage.Pickers;
using Windows.ApplicationModel.DataTransfer;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class WorkspacePage : Page, IStudioRefreshable
{
    private readonly StudioSessionService _session = App.Services.Session;
    private CancellationTokenSource? _operationCts;
    private IReadOnlyList<ProjectDto> _projects = [];
    private ProjectResponse? _projectResponse;
    private WorkspaceAssetsResponse? _assets;
    private ProjectHealthResponse? _health;
    private ProjectRelinkResponse? _relinkSuggestions;
    private MusicGraphResponse? _musicGraph;
    private LiveCuesResponse? _liveCues;
    private LiveAssetsResponse? _liveAssets;
    private PlanDto? _generatedPlan;
    private string? _pendingAudioPath;
    private string? _pendingReferencePath;
    private int _loadVersion;
    private bool _isSynchronizingSelection;
    private JsonObject? _directorDocument;
    private long _directorRevision;
    private string? _directorProjectId;
    private string? _directorReviewedJobId;
    private string? _directorDraftJobId;
    private string? _directorDraftJobStatus;
    private DirectorGenerationRequest? _directorPendingRequest;
    private JsonElement? _directorReadiness;
    private JsonObject? _workflowDocument;
    private JsonObject? _workflowSavedDocument;
    private string? _workflowProjectId;
    private string? _workflowDraftId;
    private string _workflowStatus = "not_prepared";
    private long _workflowRevision;
    private bool _workflowWriteInProgress;

    public WorkspacePage()
    {
        InitializeComponent();
    }

    public ObservableCollection<WorkspaceAssetItem> AssetItems { get; } = [];

    public ObservableCollection<WorkspaceVariantItem> VariantItems { get; } = [];

    public ObservableCollection<WorkspaceStoryboardItem> StoryboardItems { get; } = [];

    public ObservableCollection<WorkspaceDirectionSceneItem> WorkflowSceneItems { get; } = [];

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        OverviewSelectorItem.IsSelected = true;
        SetWorkspaceMode(isStoryboard: false, isPlanner: false, isReactive: false);
        await RefreshAsync();
    }

    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        CancelCurrentOperation();
        base.OnNavigatedFrom(e);
    }

    public Task RefreshAsync(CancellationToken cancellationToken = default) =>
        RunBusyAsync("Refreshing Workspace", LoadProjectsAndWorkspaceAsync, cancellationToken);

    protected override void OnNavigatingFrom(NavigatingCancelEventArgs e)
    {
        if (ProtectUnsavedWorkflowEdits())
        {
            e.Cancel = true;
        }
        base.OnNavigatingFrom(e);
    }

    private async Task LoadProjectsAndWorkspaceAsync(CancellationToken cancellationToken)
    {
        ProjectListResponse response = await App.Services.ApiClient.GetProjectsAsync(cancellationToken);
        _projects = response.Projects;

        _isSynchronizingSelection = true;
        ProjectComboBox.ItemsSource = _projects;

        string? selectedProjectId = _session.ActiveProjectId;
        ProjectDto? selectedProject = _projects.FirstOrDefault(project => project.Id == selectedProjectId)
            ?? _projects.FirstOrDefault();

        ProjectComboBox.SelectedItem = selectedProject;
        _isSynchronizingSelection = false;

        if (selectedProject is null)
        {
            ClearWorkspace("Create or open a project to begin.");
            return;
        }

        if (_session.ActiveProjectId != selectedProject.Id)
        {
            _session.ActiveProjectId = selectedProject.Id;
        }

        await LoadSelectedProjectAsync(selectedProject.Id, cancellationToken);
    }

    private async void WorkspaceMode_SelectionChanged(SelectorBar sender, SelectorBarSelectionChangedEventArgs args)
    {
        bool isStoryboard = sender.SelectedItem == StoryboardSelectorItem;
        bool isPlanner = sender.SelectedItem == PlannerSelectorItem;
        bool isReactive = sender.SelectedItem == ReactiveSelectorItem;
        SetWorkspaceMode(isStoryboard, isPlanner, isReactive);
        if (isStoryboard)
        {
            PopulateStoryboard();
        }
        else if (isPlanner)
        {
            EnsureSpecialistPage(WorkspacePlannerFrame, typeof(AiPlannerLabPage));
        }
        else if (isReactive)
        {
            EnsureSpecialistPage(WorkspaceReactiveFrame, typeof(ReactiveLabPage));
        }

        if (!isStoryboard && !isPlanner && !isReactive && IsLoaded && TryGetActiveProjectId(out string projectId))
        {
            await RunBusyAsync("Refreshing Workspace direction", async token =>
            {
                await RefreshProjectSnapshotAsync(projectId, token);
                await LoadWorkflowAsync(projectId, token);
            });
        }
    }

    private async void ProjectComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isSynchronizingSelection || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            return;
        }

        if (_session.ActiveProjectId != project.Id)
        {
            if (ProtectUnsavedWorkflowEdits())
            {
                _isSynchronizingSelection = true;
                ProjectComboBox.SelectedItem = _projects.FirstOrDefault(item => item.Id == _session.ActiveProjectId);
                _isSynchronizingSelection = false;
                return;
            }
            _session.ActiveProjectId = project.Id;
            _generatedPlan = null;
        }

        await RunBusyAsync(
            $"Opening {project.Name}",
            cancellationToken => LoadSelectedProjectAsync(project.Id, cancellationToken));
    }

    private async void RefreshWorkspaceButton_Click(object sender, RoutedEventArgs e)
    {
        await RefreshAsync();
    }

    private async void ChooseAudioButton_Click(object sender, RoutedEventArgs e)
    {
        string? path = await PickFileAsync(
            "Select audio",
            [".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"]);
        if (path is null)
        {
            return;
        }

        _pendingAudioPath = path;
        PendingAudioText.Text = $"Selected: {Path.GetFileName(path)}";
        ShowStatus(
            "Audio selected",
            "Choose Upload + analyze to add the audio and refresh the analysis.",
            InfoBarSeverity.Informational);
    }

    private async void UploadAnalyzeButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId) || ProtectUnsavedWorkflowEdits())
        {
            return;
        }

        await RunBusyAsync("Uploading and analyzing audio", async cancellationToken =>
        {
            if (!string.IsNullOrWhiteSpace(_pendingAudioPath))
            {
                await using FileStream stream = File.OpenRead(_pendingAudioPath);
                await App.Services.ApiClient.UploadAudioAsync(
                    projectId,
                    stream,
                    Path.GetFileName(_pendingAudioPath),
                    GetAudioContentType(_pendingAudioPath),
                    cancellationToken);
            }

            AnalysisResponse analysis = await App.Services.ApiClient.AnalyzeAudioAsync(projectId, cancellationToken);
            SetLastOperationJson(analysis);
            _pendingAudioPath = null;
            PendingAudioText.Text = "No local audio selected.";
            await LoadSelectedProjectAsync(projectId, cancellationToken);
            ShowStatus(
                "Audio analysis ready",
                analysis.Ok
                    ? _workflowStatus == "draft"
                        ? "Audio analysis, Director scenes, and the reactive schedule are ready for review below."
                        : "Audio analysis was refreshed. Check the Director scene draft status below."
                    : "The backend returned an incomplete analysis result.",
                analysis.Ok ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        });
    }

    private async void ChooseReferenceButton_Click(object sender, RoutedEventArgs e)
    {
        string? path = await PickFileAsync(
            "Select a reference image",
            [".png", ".jpg", ".jpeg", ".webp", ".bmp"]);
        if (path is null)
        {
            return;
        }

        _pendingReferencePath = path;
        PendingReferenceText.Text = $"Selected: {Path.GetFileName(path)}";
        ShowStatus(
            "Reference selected",
            "Choose Upload reference to add the image to this project.",
            InfoBarSeverity.Informational);
    }

    private async void UploadReferenceButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        if (string.IsNullOrWhiteSpace(_pendingReferencePath))
        {
            ShowStatus(
                "Choose a reference image",
                "Select a local image before uploading.",
                InfoBarSeverity.Warning);
            return;
        }

        string path = _pendingReferencePath;

        await RunBusyAsync("Uploading reference image", async cancellationToken =>
        {
            await using FileStream stream = File.OpenRead(path);
            await App.Services.ApiClient.UploadReferenceAssetAsync(
                projectId,
                stream,
                Path.GetFileName(path),
                GetImageContentType(path),
                cancellationToken);
            _pendingReferencePath = null;
            PendingReferenceText.Text = "No local reference selected.";
            await LoadSelectedProjectAsync(projectId, cancellationToken);
            ShowStatus(
                "Reference uploaded",
                $"{Path.GetFileName(path)} is now available to the project.",
                InfoBarSeverity.Success);
        });
    }

    private async void RefreshAssetsButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        await RunBusyAsync("Refreshing project assets", async cancellationToken =>
        {
            _assets = await App.Services.ApiClient.GetProjectAssetsAsync(projectId, cancellationToken);
            PopulateAssets();
            SetLastOperationJson(_assets);
            ShowStatus("Assets refreshed", $"{AssetItems.Count} project assets are indexed.", InfoBarSeverity.Success);
        });
    }

    private void OpenAssetButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { DataContext: WorkspaceAssetItem asset })
        {
            return;
        }

        if (!File.Exists(asset.Path))
        {
            ShowStatus(
                "Asset is backend-relative",
                "This asset cannot be safely opened as a local file. Use the relink and collect tools to make it portable.",
                InfoBarSeverity.Warning);
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo(asset.Path) { UseShellExecute = true });
        }
        catch (Exception exception)
        {
            ShowStatus("Could not open asset", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void GeneratePlanButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId) || ProtectUnsavedWorkflowEdits())
        {
            return;
        }

        await RunBusyAsync("Generating plan variants", async cancellationToken =>
        {
            string mode = GetSelectedPlanMode();
            var request = new PlanRequest(
                NullIfWhiteSpace(PlanTitleTextBox.Text),
                NullIfWhiteSpace(PlanNotesTextBox.Text),
                null,
                NumberOfVariants: 3,
                MaximumScenes: 12,
                ExpectedRevision: StudioPageHelpers.ExpectedRevision(_projectResponse?.Project));

            _generatedPlan = await App.Services.ApiClient.GeneratePlanAsync(
                projectId,
                request,
                mode,
                cancellationToken);
            await RefreshProjectSnapshotAsync(projectId, cancellationToken);
            await LoadWorkflowAsync(projectId, cancellationToken);
            SynchronizeVariantSelection();
            PopulatePlanning();
            SetLastOperationJson(_generatedPlan);
            ShowStatus(
                "Plan variants ready",
                $"{CurrentVariants.Count} variants were generated in {mode} mode.",
                InfoBarSeverity.Success);
        });
    }

    private void VariantRadioButtons_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isSynchronizingSelection)
        {
            return;
        }

        int index = VariantRadioButtons.SelectedIndex;
        if (index < 0 || index >= CurrentVariants.Count)
        {
            return;
        }

        _session.SelectedVariantIndex = index;
        PopulateSelectedVariant();
    }

    private async void AppendTimelineButton_Click(object sender, RoutedEventArgs e)
    {
        await ApplySelectedVariantAsync(overwrite: false);
    }

    private async void OverwriteTimelineButton_Click(object sender, RoutedEventArgs e)
    {
        await ApplySelectedVariantAsync(overwrite: true);
    }

    private async Task ApplySelectedVariantAsync(bool overwrite)
    {
        if (!TryGetActiveProjectId(out string projectId) || !TryGetSelectedVariant(out _))
        {
            return;
        }

        await RunBusyAsync(overwrite ? "Replacing timeline" : "Appending to timeline", async cancellationToken =>
        {
            ApplyPlanToTimelineResponse response = await App.Services.ApiClient.ApplyPlanToTimelineAsync(
                projectId,
                _session.SelectedVariantIndex,
                overwrite,
                StudioPageHelpers.ExpectedRevision(_projectResponse?.Project),
                cancellationToken);
            await RefreshProjectSnapshotAsync(projectId, cancellationToken);
            SetLastOperationJson(response);
            ShowStatus(
                "Timeline updated",
                overwrite ? "The timeline now matches the selected variant." : "The selected variant was appended to the timeline.",
                InfoBarSeverity.Success);
        });
    }

    private async void RefreshHealthButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        await RunBusyAsync("Checking project health", async cancellationToken =>
        {
            Task<ProjectHealthResponse> healthTask =
                App.Services.ApiClient.GetProjectHealthAsync(projectId, cancellationToken);
            Task<ProjectRelinkResponse> relinkTask =
                App.Services.ApiClient.GetProjectRelinkSuggestionsAsync(projectId, cancellationToken);
            await Task.WhenAll(healthTask, relinkTask);
            _health = await healthTask;
            _relinkSuggestions = await relinkTask;
            PopulateHealth();
            SetLastOperationJson(_health);
            ShowStatus("Health check complete", HealthSummaryText.Text, InfoBarSeverity.Success);
        });
    }

    private async void CollectProjectButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        await RunBusyAsync("Collecting project assets", async cancellationToken =>
        {
            ProjectCollectResponse response =
                await App.Services.ApiClient.CollectProjectAsync(projectId, cancellationToken);
            SetLastOperationJson(response);
            await LoadSelectedProjectAsync(projectId, cancellationToken);
            ShowStatus(
                "Project collection complete",
                $"{response.CopiedCount} assets copied; {response.SkippedCount} skipped.",
                InfoBarSeverity.Success);
        });
    }

    private async void RefreshLiveDataButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        await RunBusyAsync("Refreshing live project data", async cancellationToken =>
        {
            Task<MusicGraphResponse> musicTask =
                App.Services.ApiClient.GetProjectMusicGraphAsync(projectId, cancellationToken);
            Task<LiveCuesResponse> cuesTask =
                App.Services.ApiClient.GetProjectLiveCuesAsync(projectId, cancellationToken);
            Task<LiveAssetsResponse> assetsTask =
                App.Services.ApiClient.GetProjectLiveAssetsAsync(projectId, cancellationToken);
            await Task.WhenAll(musicTask, cuesTask, assetsTask);
            _musicGraph = await musicTask;
            _liveCues = await cuesTask;
            _liveAssets = await assetsTask;
            PopulateLiveData();
            SetLastOperationJson(_musicGraph);
            ShowStatus("Live data refreshed", "Music Graph, cues, and live packs are current.", InfoBarSeverity.Success);
        });
    }

    private async void ExportTemplateButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        await RunBusyAsync("Exporting template package", async cancellationToken =>
        {
            ExportTemplatePackageResponse response =
                await App.Services.ApiClient.ExportProjectTemplatePackageAsync(projectId, cancellationToken);
            TemplateJsonTextBox.Text = ToJson(response.Package);
            TemplateFileStatusText.Text = $"Exported template package schema v{response.Package.SchemaVersion}.";
            SetLastOperationJson(response);
            ShowStatus("Template preview ready", "Review or copy the backend-aligned JSON package below.", InfoBarSeverity.Success);
        });
    }

    private async void ChooseTemplateButton_Click(object sender, RoutedEventArgs e)
    {
        string? path = await PickFileAsync("Select template package", [".json"]);
        if (path is null)
        {
            return;
        }

        try
        {
            string json = await File.ReadAllTextAsync(path);
            WorkspaceModelHelpers.ParseTemplatePackage(json);
            TemplateJsonTextBox.Text = json;
            TemplateFileStatusText.Text = $"Validated: {Path.GetFileName(path)}";
            ShowStatus("Template validated", "The package is ready to import.", InfoBarSeverity.Success);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or ArgumentException)
        {
            TemplateFileStatusText.Text = "Template validation failed.";
            ShowStatus("Invalid template package", exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void ImportTemplateButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        TemplatePackageDto package;
        try
        {
            package = WorkspaceModelHelpers.ParseTemplatePackage(TemplateJsonTextBox.Text);
        }
        catch (Exception exception) when (exception is JsonException or ArgumentException)
        {
            ShowStatus("Invalid template package", exception.Message, InfoBarSeverity.Error);
            return;
        }

        await RunBusyAsync("Importing template package", async cancellationToken =>
        {
            ImportTemplatePackageResponse response =
                await App.Services.ApiClient.ImportProjectTemplatePackageAsync(
                    projectId,
                    package,
                    TemplateMergeCheckBox.IsChecked == true,
                    StudioPageHelpers.ExpectedRevision(_projectResponse?.Project),
                    cancellationToken);
            _generatedPlan = null;
            await LoadSelectedProjectAsync(projectId, cancellationToken);

            SetLastOperationJson(response);
            TemplateFileStatusText.Text = response.Ok ? "Template imported." : "Template import was incomplete.";
            ShowStatus(
                response.Ok ? "Template imported" : "Template import warning",
                response.Applied.Count > 0
                    ? $"Applied: {string.Join(", ", response.Applied)}."
                    : "The backend did not report any applied fields.",
                response.Ok ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        });
    }

    private void CopyAnalysisJsonButton_Click(object sender, RoutedEventArgs e) =>
        CopyTextToClipboard(AnalysisJsonTextBox.Text, "Analysis JSON copied.");

    private void CopyVariantJsonButton_Click(object sender, RoutedEventArgs e) =>
        CopyTextToClipboard(VariantJsonTextBox.Text, "Variant JSON copied.");

    private async void MoveSceneEarlierButton_Click(object sender, RoutedEventArgs e)
    {
        await MoveSceneAsync(sender, -1);
    }

    private async void MoveSceneLaterButton_Click(object sender, RoutedEventArgs e)
    {
        await MoveSceneAsync(sender, 1);
    }

    private async Task MoveSceneAsync(object sender, int offset)
    {
        if (sender is not Button { DataContext: WorkspaceStoryboardItem item } ||
            !TryGetActiveProjectId(out string projectId) ||
            !TryGetSelectedVariant(out PlanVariantDto variant))
        {
            return;
        }

        IReadOnlyList<PlanSceneDto> reordered =
            WorkspaceModelHelpers.MoveScene(variant.Scenes, item.Index, offset);
        if (reordered.SequenceEqual(variant.Scenes))
        {
            return;
        }

        int selectedIndex = Math.Clamp(item.Index + offset, 0, reordered.Count - 1);
        await RunBusyAsync("Saving storyboard order", async cancellationToken =>
        {
            UpdatePlanVariantResponse response = await App.Services.ApiClient.UpdatePlanVariantAsync(
                projectId,
                _session.SelectedVariantIndex,
                reordered,
                StudioPageHelpers.ExpectedRevision(_projectResponse?.Project),
                cancellationToken);
            SetLastOperationJson(response);
            _generatedPlan = null;
            await LoadSelectedProjectAsync(projectId, cancellationToken);
            StoryboardListView.SelectedIndex = selectedIndex;
            ShowStatus("Storyboard order saved", "The selected variant was updated on the backend.", InfoBarSeverity.Success);
        });
    }

    private void NavigatePlannerButton_Click(object sender, RoutedEventArgs e) => NavigateTo("plannerLab");

    private void NavigateReactiveButton_Click(object sender, RoutedEventArgs e) => NavigateTo("reactiveLab");

    private void NavigateTimelineButton_Click(object sender, RoutedEventArgs e) => NavigateTo("timeline");

    private void NavigateRenderButton_Click(object sender, RoutedEventArgs e) => NavigateTo("render");

    private void NavigateOutputsButton_Click(object sender, RoutedEventArgs e) => NavigateTo("outputs");

    private void NavigateQueueButton_Click(object sender, RoutedEventArgs e) => NavigateTo("queue");

    private async Task LoadSelectedProjectAsync(string projectId, CancellationToken cancellationToken)
    {
        int loadVersion = ++_loadVersion;
        ProjectResponse project = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        if (loadVersion != _loadVersion || _session.ActiveProjectId != projectId)
        {
            return;
        }

        _projectResponse = project;
        PopulateProject();
        await LoadWorkflowAsync(projectId, cancellationToken);
        await LoadDirectorAsync(projectId, cancellationToken);
        await LoadOptionalWorkspaceDataAsync(projectId, cancellationToken, loadVersion);
    }

    private async Task RefreshProjectSnapshotAsync(string projectId, CancellationToken cancellationToken)
    {
        ProjectResponse response = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        if (_session.ActiveProjectId != projectId)
        {
            return;
        }

        _projectResponse = response;
        PopulateProject();
    }

    private async Task LoadOptionalWorkspaceDataAsync(
        string projectId,
        CancellationToken cancellationToken,
        int? expectedLoadVersion = null)
    {
        int loadVersion = expectedLoadVersion ?? _loadVersion;
        var warnings = new ConcurrentBag<string>();

        Task<WorkspaceAssetsResponse?> assetsTask = LoadOptionalAsync(
            "assets",
            token => App.Services.ApiClient.GetProjectAssetsAsync(projectId, token),
            warnings,
            cancellationToken);
        Task<ProjectHealthResponse?> healthTask = LoadOptionalAsync(
            "health",
            token => App.Services.ApiClient.GetProjectHealthAsync(projectId, token),
            warnings,
            cancellationToken);
        Task<ProjectRelinkResponse?> relinkTask = LoadOptionalAsync(
            "relink suggestions",
            token => App.Services.ApiClient.GetProjectRelinkSuggestionsAsync(projectId, token),
            warnings,
            cancellationToken);
        Task<MusicGraphResponse?> musicTask = LoadOptionalAsync(
            "Music Graph",
            token => App.Services.ApiClient.GetProjectMusicGraphAsync(projectId, token),
            warnings,
            cancellationToken);
        Task<LiveCuesResponse?> cuesTask = LoadOptionalAsync(
            "live cues",
            token => App.Services.ApiClient.GetProjectLiveCuesAsync(projectId, token),
            warnings,
            cancellationToken);
        Task<LiveAssetsResponse?> liveAssetsTask = LoadOptionalAsync(
            "live assets",
            token => App.Services.ApiClient.GetProjectLiveAssetsAsync(projectId, token),
            warnings,
            cancellationToken);

        await Task.WhenAll(assetsTask, healthTask, relinkTask, musicTask, cuesTask, liveAssetsTask);
        if (loadVersion != _loadVersion || _session.ActiveProjectId != projectId)
        {
            return;
        }

        _assets = await assetsTask;
        _health = await healthTask;
        _relinkSuggestions = await relinkTask;
        _musicGraph = await musicTask;
        _liveCues = await cuesTask;
        _liveAssets = await liveAssetsTask;
        PopulateOptionalWorkspaceData();

        if (warnings.Count > 0)
        {
            ShowStatus(
                "Workspace opened with limited data",
                $"Could not refresh {string.Join(", ", warnings)}. Core project editing remains available.",
                InfoBarSeverity.Warning);
        }
    }

    private static async Task<T?> LoadOptionalAsync<T>(
        string label,
        Func<CancellationToken, Task<T>> loader,
        ConcurrentBag<string> warnings,
        CancellationToken cancellationToken)
        where T : class
    {
        try
        {
            return await loader(cancellationToken);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch
        {
            warnings.Add(label);
            return null;
        }
    }

    private void PopulateProject()
    {
        ProjectDto? project = _projectResponse?.Project;
        if (project is null)
        {
            ClearWorkspace("The selected project could not be loaded.");
            return;
        }

        ProjectStatusText.Text =
            $"{project.Name}  •  schema v{project.SchemaVersion}  •  " +
            $"{(project.HasAudio ? project.AudioFileName : "audio missing")}  •  " +
            $"{(project.HasAnalysis ? "analysis ready" : "analysis pending")}";

        PopulateAnalysis(project);
        PopulatePlanning();
        PopulateMetadata(project);
    }

    private async Task LoadWorkflowAsync(string projectId, CancellationToken cancellationToken, bool discardLocalEdits = false)
    {
        JsonElement response = await App.Services.ApiClient.GetDirectorWorkflowAsync(projectId, cancellationToken);
        cancellationToken.ThrowIfCancellationRequested();
        if (_session.ActiveProjectId != projectId)
        {
            return;
        }

        if (!discardLocalEdits && _workflowProjectId == projectId && HasUnsavedWorkflowEdits())
        {
            // Refresh may run when another Workspace tab changes the project. Preserve the
            // user's local JSON and only allow saving against the same current draft.
            string? latestId = response.TryGetProperty("draft", out JsonElement draft) && draft.ValueKind == JsonValueKind.Object
                ? draft.GetProperty("draft_id").GetString() : null;
            string latestStatus = response.GetProperty("status").GetString() ?? "not_prepared";
            if (latestId == _workflowDraftId && latestStatus == "draft")
            {
                _workflowRevision = response.GetProperty("revision").GetInt64();
                WorkflowStatusText.Text = "Your local draft edits are retained. Save or apply them when ready.";
            }
            else
            {
                _workflowStatus = "stale";
                SaveWorkflowButton.IsEnabled = false;
                ApplyWorkflowButton.IsEnabled = false;
                WorkflowStatusText.Text = "The shared draft changed. Your local edits are retained; copy anything you need before discarding them and loading the current draft.";
            }
            return;
        }

        ApplyWorkflowResponse(projectId, response);
    }

    private void ApplyWorkflowResponse(string projectId, JsonElement response)
    {
        _workflowProjectId = projectId;
        _workflowRevision = response.GetProperty("revision").GetInt64();
        _workflowStatus = response.GetProperty("status").GetString() ?? "not_prepared";
        if (response.TryGetProperty("plan", out JsonElement plan) && plan.ValueKind == JsonValueKind.Object)
        {
            _generatedPlan = JsonSerializer.Deserialize(plan.GetRawText(), StudioJson.GetTypeInfo<PlanDto>());
            PopulatePlanning();
        }
        _workflowDocument = null;
        _workflowSavedDocument = null;
        _workflowDraftId = null;
        WorkflowSceneItems.Clear();
        WorkflowThemeTextBox.Text = string.Empty;
        WorkflowStyleTextBox.Text = string.Empty;
        WorkflowSummaryText.Text = string.Empty;
        bool editable = _workflowStatus == "draft";
        WorkflowThemeTextBox.IsEnabled = editable;
        WorkflowStyleTextBox.IsEnabled = editable;
        SaveWorkflowButton.IsEnabled = editable;
        ApplyWorkflowButton.IsEnabled = editable;
        DiscardWorkflowEditsButton.IsEnabled = false;
        PrepareWorkflowButton.IsEnabled = _projectResponse?.Project.HasAnalysis == true;

        if (!response.TryGetProperty("draft", out JsonElement draft) || draft.ValueKind != JsonValueKind.Object)
        {
            WorkflowStatusText.Text = response.TryGetProperty("preparation_error", out JsonElement error) && error.ValueKind == JsonValueKind.String
                ? $"Audio analysis is saved, but direction preparation needs attention: {error.GetString()}"
                : "Analyze audio to automatically prepare Director scenes and the reactive schedule.";
            return;
        }

        _workflowDraftId = draft.GetProperty("draft_id").GetString();
        _workflowDocument = JsonNode.Parse(draft.GetProperty("document").GetRawText())!.AsObject();
        JsonObject? bible = _workflowDocument["story_bible"] as JsonObject;
        WorkflowThemeTextBox.Text = bible?["project_theme"]?.GetValue<string>() ?? string.Empty;
        WorkflowStyleTextBox.Text = bible?["visual_style"]?.GetValue<string>() ?? string.Empty;
        int sampleRate = draft.TryGetProperty("provenance", out JsonElement provenance) &&
                         provenance.TryGetProperty("sample_rate", out JsonElement rate) && rate.TryGetInt32(out int value) && value > 0
            ? value : 48_000;
        foreach (JsonObject scene in (_workflowDocument["scenes"] as JsonArray ?? []).OfType<JsonObject>())
        {
            WorkflowSceneItems.Add(new WorkspaceDirectionSceneItem(scene, sampleRate, editable, WorkflowEditsChanged));
        }
        _workflowSavedDocument = BuildWorkflowDocument();
        int cameraCount = 0;
        int motionCount = 0;
        int markerCount = 0;
        if (draft.TryGetProperty("schedule", out JsonElement schedule))
        {
            cameraCount = WorkflowArrayCount(schedule, "camera_keys");
            motionCount = WorkflowArrayCount(schedule, "motion_keys");
            markerCount = WorkflowArrayCount(schedule, "markers");
        }
        string analysisRevision = _workflowDocument["analysis_revision"]?.ToString() ?? "unavailable";
        WorkflowSummaryText.Text = $"{WorkflowSceneItems.Count} scenes · {cameraCount} camera keys · {motionCount} motion keys · {markerCount} markers · analysis revision {analysisRevision}";
        WorkflowStatusText.Text = _workflowStatus switch
        {
            "applied" => "Direction and its reactive schedule are applied. AI Planner and Reactive Lab use this shared Workspace result.",
            "stale" => "Analysis, direction, or the timeline changed. Prepare a current draft before applying it.",
            _ => "Director scenes and the reactive schedule are ready together. Review here or in AI Planner and Reactive Lab, then apply once."
        };
        if (draft.TryGetProperty("warnings", out JsonElement warnings) && warnings.ValueKind == JsonValueKind.Array)
        {
            string detail = string.Join(" ", warnings.EnumerateArray().Select(item => item.GetString()).Where(item => !string.IsNullOrWhiteSpace(item)));
            if (!string.IsNullOrWhiteSpace(detail))
            {
                WorkflowStatusText.Text += Environment.NewLine + detail;
            }
        }
        UpdateWorkflowEditingAvailability();
    }

    private static int WorkflowArrayCount(JsonElement value, string property) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out JsonElement array) && array.ValueKind == JsonValueKind.Array
            ? array.GetArrayLength() : 0;

    private JsonObject BuildWorkflowDocument()
    {
        JsonObject document = _workflowDocument?.DeepClone().AsObject()
            ?? throw new InvalidOperationException("The shared Workspace draft has not loaded yet.");
        JsonObject bible = document["story_bible"]?.AsObject() ?? new JsonObject();
        bible["project_theme"] = WorkflowThemeTextBox.Text;
        bible["visual_style"] = WorkflowStyleTextBox.Text;
        document["story_bible"] = bible;
        return document;
    }

    private bool HasUnsavedWorkflowEdits() => _workflowDocument is not null && _workflowSavedDocument is not null &&
        !JsonNode.DeepEquals(BuildWorkflowDocument(), _workflowSavedDocument);

    private bool ProtectUnsavedWorkflowEdits()
    {
        if (!HasUnsavedWorkflowEdits())
        {
            return false;
        }
        ShowStatus("Draft edits are unsaved", "Save or apply your draft edits in Overview + Director before continuing, or choose Discard local edits to reload the saved draft.", InfoBarSeverity.Warning);
        return true;
    }

    private void WorkflowEditsChanged() => DiscardWorkflowEditsButton.IsEnabled = HasUnsavedWorkflowEdits();

    private void UpdateWorkflowEditingAvailability()
    {
        bool editable = _workflowStatus == "draft" && !_workflowWriteInProgress;
        WorkflowThemeTextBox.IsEnabled = editable;
        WorkflowStyleTextBox.IsEnabled = editable;
        WorkflowScenesList.IsEnabled = editable;
        SaveWorkflowButton.IsEnabled = editable;
        ApplyWorkflowButton.IsEnabled = editable;
        DiscardWorkflowEditsButton.IsEnabled = !_workflowWriteInProgress && HasUnsavedWorkflowEdits();
        PrepareWorkflowButton.IsEnabled = !_workflowWriteInProgress && _projectResponse?.Project.HasAnalysis == true;
    }

    private void WorkflowInputs_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (DiscardWorkflowEditsButton is not null)
        {
            WorkflowEditsChanged();
        }
    }

    private async void DiscardWorkflowEditsButton_Click(object sender, RoutedEventArgs e)
    {
        if (_workflowProjectId is not string projectId || projectId != _session.ActiveProjectId)
        {
            return;
        }
        await RunBusyAsync("Reloading saved Workspace draft", token => LoadWorkflowAsync(projectId, token, discardLocalEdits: true));
    }

    private async void PrepareWorkflowButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetActiveProjectId(out string projectId) || ProtectUnsavedWorkflowEdits())
        {
            return;
        }
        await RunBusyAsync("Preparing Workspace direction and reactive schedule", async token =>
        {
            await RefreshProjectSnapshotAsync(projectId, token);
            token.ThrowIfCancellationRequested();
            if (_session.ActiveProjectId != projectId)
            {
                return;
            }
            JsonElement response = await App.Services.ApiClient.PrepareDirectorWorkflowAsync(
                projectId, new DirectorApplyRequest(_projectResponse!.Project.Revision), token);
            token.ThrowIfCancellationRequested();
            if (_session.ActiveProjectId != projectId)
            {
                return;
            }
            ApplyWorkflowResponse(projectId, response);
            await RefreshProjectSnapshotAsync(projectId, token);
        });
    }

    private async void SaveWorkflowButton_Click(object sender, RoutedEventArgs e) => await ReviewWorkflowAsync(apply: false);

    private async void ApplyWorkflowButton_Click(object sender, RoutedEventArgs e) => await ReviewWorkflowAsync(apply: true);

    private async Task ReviewWorkflowAsync(bool apply)
    {
        if (_workflowProjectId is not string projectId || _workflowDraftId is not string draftId ||
            _session.ActiveProjectId != projectId || _workflowStatus != "draft")
        {
            return;
        }
        using JsonDocument payload = JsonDocument.Parse(BuildWorkflowDocument().ToJsonString());
        JsonElement document = payload.RootElement.Clone();
        var request = new DirectorWorkflowReviewRequest(_workflowRevision, draftId, document);
        _workflowWriteInProgress = true;
        UpdateWorkflowEditingAvailability();
        try
        {
            await RunBusyAsync(apply ? "Applying Workspace direction and reactive schedule" : "Saving Workspace draft edits", async token =>
            {
                JsonElement response = apply
                    ? await App.Services.ApiClient.ApplyDirectorWorkflowAsync(projectId, request, token)
                    : await App.Services.ApiClient.ReviewDirectorWorkflowAsync(projectId, request, token);
                token.ThrowIfCancellationRequested();
                if (_session.ActiveProjectId != projectId)
                {
                    return;
                }
                ApplyWorkflowResponse(projectId, response);
                await RefreshProjectSnapshotAsync(projectId, token);
                if (apply)
                {
                    await LoadDirectorAsync(projectId, token);
                }
                ShowStatus(apply ? "Workspace direction applied" : "Workspace draft saved",
                    apply ? "Director scenes and their reactive schedule are now available to Timeline and Render."
                        : "Your draft edits and updated reactive schedule are saved for review across Workspace tabs.",
                    InfoBarSeverity.Success);
            });
        }
        finally
        {
            _workflowWriteInProgress = false;
            UpdateWorkflowEditingAvailability();
        }
    }

    private async Task LoadDirectorAsync(string projectId, CancellationToken cancellationToken)
    {
        JsonElement response = await App.Services.ApiClient.GetDirectorDocumentAsync(projectId, cancellationToken);
        cancellationToken.ThrowIfCancellationRequested();
        if (_session.ActiveProjectId != projectId)
        {
            return;
        }

        if (_directorProjectId == projectId && _directorDocument is not null && !WorkspaceDirectorInputsMatchDocument())
        {
            WorkspaceDirectorStatusText.Text = "Your unsaved advanced direction edits are retained. Save them before loading another direction.";
            return;
        }

        _directorProjectId = projectId;
        _directorPendingRequest = null;
        _directorReviewedJobId = null;
        _directorDraftJobId = null;
        _directorDraftJobStatus = null;
        WorkspaceDirectorDraftTextBox.Text = string.Empty;
        ApplyDirectorWorkspaceDocument(response);
        DirectorSessionText.Text =
            $"{_projectResponse?.Project.Name ?? projectId} · " +
            $"{CurrentVariants.ElementAtOrDefault(_session.SelectedVariantIndex)?.Scenes.Count ?? 0} selected storyboard scene(s) · " +
            "analysis and timeline data remain shared with this Workspace session.";
        WorkspaceDirectorStatusText.Text = "Save direction before preparing prompts or generating a draft.";
        await LoadDirectorReadinessAsync(projectId, cancellationToken);
        UpdateDirectorWorkspaceAvailability();
    }

    private string SelectedDirectorMode()
    {
        return (WorkspaceDirectorModeComboBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "automatic";
    }

    private string SelectedDirectorReadinessEngine()
    {
        return (WorkspaceDirectorReadinessEngineComboBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "automatic";
    }

    private async Task LoadDirectorReadinessAsync(string projectId, CancellationToken cancellationToken)
    {
        try
        {
            JsonElement response = await App.Services.ApiClient.GetDirectorReadinessAsync(
                projectId,
                SelectedDirectorMode(),
                SelectedDirectorReadinessEngine(),
                cancellationToken);
            if (_session.ActiveProjectId != projectId)
            {
                return;
            }

            _directorReadiness = response;
            WorkspaceDirectorReadinessText.Text = FormatDirectorReadiness(response);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            _directorReadiness = null;
            WorkspaceDirectorReadinessText.Text = $"Readiness unavailable: {exception.Message}";
        }
    }

    private static string FormatDirectorReadiness(JsonElement response)
    {
        string mode = response.TryGetProperty("requested_mode", out JsonElement modeValue)
            ? modeValue.GetString() ?? "automatic"
            : "automatic";
        string tier = response.TryGetProperty("hardware_tier", out JsonElement tierValue)
            ? tierValue.GetString() ?? "unknown"
            : "unknown";
        string profile = response.TryGetProperty("profile", out JsonElement profileValue)
            ? profileValue.GetString() ?? "pending"
            : "pending";
        bool ready = response.TryGetProperty("ready", out JsonElement readyValue) && readyValue.ValueKind == JsonValueKind.True;
        string director = "Director pending";
        if (response.TryGetProperty("director", out JsonElement directorValue) && directorValue.ValueKind == JsonValueKind.Object)
        {
            string label = directorValue.TryGetProperty("label", out JsonElement labelValue) ? labelValue.GetString() ?? "Director" : "Director";
            string directorProfile = directorValue.TryGetProperty("profile", out JsonElement directorProfileValue) ? directorProfileValue.GetString() ?? "profile pending" : "profile pending";
            director = $"Director: {label} ({directorProfile})";
        }
        string renderer = "Renderer pending";
        if (response.TryGetProperty("renderer", out JsonElement rendererValue) && rendererValue.ValueKind == JsonValueKind.Object)
        {
            string label = rendererValue.TryGetProperty("label", out JsonElement labelValue) ? labelValue.GetString() ?? "Renderer" : "Renderer";
            string rendererProfile = rendererValue.TryGetProperty("profile", out JsonElement rendererProfileValue) ? rendererProfileValue.GetString() ?? "profile pending" : "profile pending";
            renderer = $"Renderer: {label} ({rendererProfile})";
        }

        List<string> lines =
        [
            $"Mode: {mode} · hardware tier: {tier} · profile: {profile} · {(ready ? "ready" : "blocked")}",
            director,
            renderer,
        ];
        if (response.TryGetProperty("blockers", out JsonElement blockers) && blockers.ValueKind == JsonValueKind.Array)
        {
            string blockerText = string.Join(" | ", blockers.EnumerateArray().Select(item => item.GetString()).Where(item => !string.IsNullOrWhiteSpace(item)));
            if (!string.IsNullOrWhiteSpace(blockerText))
            {
                lines.Add($"Blockers: {blockerText}");
            }
        }
        if (response.TryGetProperty("warnings", out JsonElement warnings) && warnings.ValueKind == JsonValueKind.Array)
        {
            string warningText = string.Join(" | ", warnings.EnumerateArray().Select(item => item.GetString()).Where(item => !string.IsNullOrWhiteSpace(item)));
            if (!string.IsNullOrWhiteSpace(warningText))
            {
                lines.Add($"Resolution notes: {warningText}");
            }
        }
        return string.Join(Environment.NewLine, lines);
    }

    private async void RefreshWorkspaceDirectorReadinessButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId)
        {
            return;
        }

        await RunBusyAsync("Refreshing Director readiness", token => LoadDirectorReadinessAsync(projectId, token));
    }

    private async void WorkspaceDirectorModeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_directorProjectId is string projectId && WorkspaceDirectorModeComboBox.SelectedItem is not null)
        {
            await RunBusyAsync("Refreshing Director readiness", token => LoadDirectorReadinessAsync(projectId, token));
        }
    }

    private async void WorkspaceDirectorReadinessEngineComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_directorProjectId is string projectId && WorkspaceDirectorReadinessEngineComboBox.SelectedItem is not null)
        {
            await RunBusyAsync("Refreshing Director readiness", token => LoadDirectorReadinessAsync(projectId, token));
        }
    }

    private void ApplyDirectorWorkspaceDocument(JsonElement response)
    {
        JsonElement document = response.TryGetProperty("document", out JsonElement nested)
            ? nested
            : response;
        _directorRevision = response.TryGetProperty("revision", out JsonElement revision)
            ? revision.GetInt64()
            : _projectResponse?.Project.Revision ?? 1;
        _directorDocument = JsonNode.Parse(document.GetRawText())?.AsObject();
        if (_directorDocument is null)
        {
            _directorDocument = new JsonObject
            {
                ["version"] = 1,
                ["story_bible"] = new JsonObject { ["revision"] = 1 },
                ["scenes"] = new JsonArray(),
            };
        }

        JsonObject bible = _directorDocument["story_bible"]?.AsObject() ?? new JsonObject();
        WorkspaceDirectorThemeTextBox.Text = bible["project_theme"]?.GetValue<string>() ?? string.Empty;
        WorkspaceDirectorStyleTextBox.Text = bible["visual_style"]?.GetValue<string>() ?? string.Empty;
        WorkspaceDirectorSceneSpecsTextBox.Text = _directorDocument["scenes"]?.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) ?? "[]";
        WorkspaceDirectorPromptPreviewTextBox.Text = string.Empty;
        _directorReviewedJobId = null;
        ApplyWorkspaceDirectorButton.IsEnabled = false;
        WorkspaceDirectorRevisionText.Text = $"Revision {_directorRevision}";
    }

    private JsonObject BuildWorkspaceDirectorDocument()
    {
        if (_directorDocument is null)
        {
            throw new InvalidOperationException("Director is still loading for this project.");
        }

        JsonNode? parsedScenes = JsonNode.Parse(WorkspaceDirectorSceneSpecsTextBox.Text);
        if (parsedScenes is not JsonArray scenes)
        {
            throw new JsonException("SceneSpecs must be a JSON array.");
        }

        JsonObject document = _directorDocument.DeepClone().AsObject();
        JsonObject bible = document["story_bible"]?.AsObject() ?? new JsonObject { ["revision"] = 1 };
        bible["project_theme"] = WorkspaceDirectorThemeTextBox.Text.Trim();
        bible["visual_style"] = WorkspaceDirectorStyleTextBox.Text.Trim();
        document["story_bible"] = bible;
        document["scenes"] = scenes;
        return document;
    }

    private bool WorkspaceDirectorInputsMatchDocument()
    {
        try
        {
            if (_directorDocument is null)
            {
                return false;
            }

            JsonObject document = BuildWorkspaceDirectorDocument();
            return string.Equals(
                       document["story_bible"]?["project_theme"]?.GetValue<string>(),
                       _directorDocument["story_bible"]?["project_theme"]?.GetValue<string>(),
                       StringComparison.Ordinal) &&
                   string.Equals(
                       document["story_bible"]?["visual_style"]?.GetValue<string>(),
                       _directorDocument["story_bible"]?["visual_style"]?.GetValue<string>(),
                       StringComparison.Ordinal) &&
                   JsonNode.DeepEquals(document["scenes"], _directorDocument["scenes"]);
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private void UpdateDirectorWorkspaceAvailability()
    {
        bool hasProject = !string.IsNullOrWhiteSpace(_directorProjectId);
        bool saved = hasProject && WorkspaceDirectorInputsMatchDocument();
        UseWorkspaceStoryboardButton.IsEnabled = hasProject && CurrentVariants.Count > 0;
        SaveWorkspaceDirectorButton.IsEnabled = hasProject && !saved;
        PrepareWorkspacePromptsButton.IsEnabled = hasProject && saved && _directorDocument?["scenes"]?.AsArray().Count > 0;
        GenerateWorkspaceDirectorButton.IsEnabled = hasProject && saved && _directorDocument?["scenes"]?.AsArray().Count > 0;
        ReviewWorkspaceDirectorButton.IsEnabled = hasProject && !string.IsNullOrWhiteSpace(_session.SelectedJobId);
        CancelWorkspaceDirectorButton.IsEnabled = hasProject &&
            !string.IsNullOrWhiteSpace(_directorDraftJobId) && _directorDraftJobId == _session.SelectedJobId &&
            _directorDraftJobStatus is "queued" or "running" or "paused";
        ApplyWorkspaceDirectorButton.IsEnabled = hasProject && !string.IsNullOrWhiteSpace(_directorReviewedJobId);
    }

    private void UseWorkspaceStoryboardButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetSelectedVariant(out PlanVariantDto variant))
        {
            ShowStatus("Storyboard required", "Generate or select a storyboard variant before staging Director scenes.", InfoBarSeverity.Warning);
            return;
        }

        var scenes = new JsonArray();
        for (int index = 0; index < variant.Scenes.Count; index++)
        {
            PlanSceneDto scene = variant.Scenes[index];
            long startSample = checked((long)Math.Round(scene.StartSeconds * 48_000, MidpointRounding.AwayFromZero));
            long endSample = checked((long)Math.Round(scene.EndSeconds * 48_000, MidpointRounding.AwayFromZero));
            var sceneObject = new JsonObject
            {
                ["scene_id"] = $"workspace-scene-{index + 1}",
                ["start_sample"] = startSample.ToString(CultureInfo.InvariantCulture),
                ["end_sample"] = endSample.ToString(CultureInfo.InvariantCulture),
                ["intent"] = string.IsNullOrWhiteSpace(scene.Prompt) ? $"Scene {index + 1}" : scene.Prompt,
                ["continuity_mode"] = "continuous",
                ["camera"] = new JsonObject
                {
                    ["shot_type"] = scene.ShotType ?? string.Empty,
                    ["movement"] = scene.Camera ?? string.Empty,
                },
                ["environment"] = new JsonObject
                {
                    ["location"] = scene.Setting ?? string.Empty,
                },
                ["renderer_hints"] = new JsonObject
                {
                    ["source"] = "workspace_storyboard",
                    ["negative_prompt"] = scene.NegativePrompt ?? string.Empty,
                    ["name"] = $"Scene {index + 1}",
                },
            };
            var actions = new JsonArray();
            if (!string.IsNullOrWhiteSpace(scene.Action))
            {
                actions.Add(scene.Action);
            }
            sceneObject["actions"] = actions;
            JsonObject environment = sceneObject["environment"]!.AsObject();
            var secondaryMotion = new JsonArray();
            if (!string.IsNullOrWhiteSpace(scene.EnvironmentMotion))
            {
                secondaryMotion.Add(scene.EnvironmentMotion);
            }
            environment["secondary_motion"] = secondaryMotion;
            if (!string.IsNullOrWhiteSpace(scene.CharacterLock))
            {
                sceneObject["subjects"] = new JsonArray
                {
                    new JsonObject
                    {
                        ["id"] = "primary",
                        ["role"] = "primary",
                        ["appearance_lock"] = true,
                        ["appearance_notes"] = new JsonArray(JsonValue.Create(scene.CharacterLock)),
                    },
                };
            }
            scenes.Add(sceneObject);
        }

        WorkspaceDirectorSceneSpecsTextBox.Text = scenes.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
        if (string.IsNullOrWhiteSpace(WorkspaceDirectorThemeTextBox.Text))
        {
            WorkspaceDirectorThemeTextBox.Text = _projectResponse?.Project.Name ?? string.Empty;
        }
        if (string.IsNullOrWhiteSpace(WorkspaceDirectorStyleTextBox.Text))
        {
            WorkspaceDirectorStyleTextBox.Text = "Cinematic continuity";
        }
        WorkspaceDirectorStatusText.Text = $"{scenes.Count} storyboard scene(s) staged for Director. Save direction to commit them.";
        UpdateDirectorWorkspaceAvailability();
    }

    private async void SaveWorkspaceDirectorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId)
        {
            return;
        }

        await RunBusyAsync("Saving Director direction", async cancellationToken =>
        {
            JsonObject document = BuildWorkspaceDirectorDocument();
            using JsonDocument payload = JsonDocument.Parse(document.ToJsonString());
            JsonElement response = await App.Services.ApiClient.SaveDirectorDocumentAsync(
                projectId,
                new DirectorUpdateRequest(_directorRevision, payload.RootElement.Clone()),
                cancellationToken);
            ApplyDirectorWorkspaceDocument(response);
            await RefreshProjectSnapshotAsync(projectId, cancellationToken);
            await LoadWorkflowAsync(projectId, cancellationToken);
            WorkspaceDirectorStatusText.Text = "Story Bible and SceneSpecs saved to the shared Workspace project.";
            ShowStatus("Director saved", "The direction is now available to Timeline, Render, Queue, and both Studio clients.", InfoBarSeverity.Success);
        });
    }

    private async void PrepareWorkspacePromptsButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId)
        {
            return;
        }
        if (!WorkspaceDirectorInputsMatchDocument())
        {
            ShowStatus("Save direction first", "Preparing prompts uses the saved project document. Save your current Workspace edits first.", InfoBarSeverity.Warning);
            return;
        }

        await RunBusyAsync("Preparing Director prompts", async cancellationToken =>
        {
            string engine = (WorkspaceDirectorEngineComboBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "hunyuan_video15";
            JsonElement response = await App.Services.ApiClient.GetDirectorPromptsAsync(projectId, engine, cancellationToken);
            WorkspaceDirectorPromptPreviewTextBox.Text = response.TryGetProperty("packages", out JsonElement packages)
                ? string.Join(Environment.NewLine + Environment.NewLine, packages.EnumerateArray().Select(item => $"[{item.GetProperty("scene_id").GetString()}] {item.GetProperty("prompt").GetString()}"))
                : string.Empty;
            WorkspaceDirectorStatusText.Text = $"Prepared {packages.GetArrayLength()} {engine} prompt package(s). No generation job was submitted.";
            ShowStatus("Prompts prepared", "The renderer-specific prompt package is ready for review.", InfoBarSeverity.Success);
        });
    }

    private async void GenerateWorkspaceDirectorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId)
        {
            return;
        }
        if (!WorkspaceDirectorInputsMatchDocument())
        {
            ShowStatus("Save direction first", "Generation requires a stable saved Workspace revision.", InfoBarSeverity.Warning);
            return;
        }
        string instruction = WorkspaceDirectorInstructionTextBox.Text.Trim();
        if (instruction.Length == 0)
        {
            ShowStatus("Direction required", "Enter an instruction for the Director draft.", InfoBarSeverity.Warning);
            return;
        }

        await RunBusyAsync("Generating Director draft", async cancellationToken =>
        {
            string mode = SelectedDirectorMode();
            string rendererEngine = SelectedDirectorReadinessEngine();
            if (_directorPendingRequest is null ||
                !string.Equals(_directorPendingRequest.Instruction, instruction, StringComparison.Ordinal) ||
                _directorPendingRequest.ExpectedRevision != _directorRevision ||
                !string.Equals(_directorPendingRequest.Mode, mode, StringComparison.Ordinal) ||
                !string.Equals(_directorPendingRequest.RendererEngine, rendererEngine, StringComparison.Ordinal))
            {
                _directorPendingRequest = new DirectorGenerationRequest(
                    _directorRevision,
                    Guid.NewGuid().ToString(),
                    instruction,
                    mode,
                    rendererEngine,
                    false);
            }
            JsonElement response = await App.Services.ApiClient.GenerateDirectorAsync(projectId, _directorPendingRequest, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (_directorProjectId != projectId || _session.ActiveProjectId != projectId)
            {
                return;
            }
            string jobId = response.GetProperty("job_id").GetString() ?? string.Empty;
            App.Services.Session.SetSelectedJob(projectId, jobId);
            _directorDraftJobId = jobId;
            _directorDraftJobStatus = response.GetProperty("status").GetString();
            _directorPendingRequest = null;
            _directorReviewedJobId = null;
            WorkspaceDirectorDraftTextBox.Text = "Draft queued. Review it here after the queue reports completion.";
            WorkspaceDirectorStatusText.Text = $"Director draft queued in the shared project queue ({jobId}).";
            UpdateDirectorWorkspaceAvailability();
            ShowStatus("Director draft queued", "Review progress or cancel the draft here. Completed drafts wait for your review and apply action.", InfoBarSeverity.Success);
        });
    }

    private async void ReviewWorkspaceDirectorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId || string.IsNullOrWhiteSpace(_session.SelectedJobId))
        {
            ShowStatus("Director job required", "Generate a draft in Workspace or select its job in Queue first.", InfoBarSeverity.Warning);
            return;
        }

        string jobId = _session.SelectedJobId!;
        await RunBusyAsync("Reviewing Director draft", async cancellationToken =>
        {
            JsonElement response = await App.Services.ApiClient.GetDirectorDraftAsync(projectId, jobId, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (_directorProjectId != projectId || _session.ActiveProjectId != projectId)
            {
                return;
            }
            string status = response.GetProperty("status").GetString() ?? "unknown";
            _directorDraftJobId = jobId;
            _directorDraftJobStatus = status;
            if (!string.Equals(status, "succeeded", StringComparison.OrdinalIgnoreCase))
            {
                _directorReviewedJobId = null;
                string message = response.TryGetProperty("error", out JsonElement error) && error.ValueKind == JsonValueKind.String
                    ? error.GetString() ?? string.Empty
                    : string.Empty;
                if (message.Length == 0 &&
                    response.TryGetProperty("progress", out JsonElement progress) && progress.ValueKind == JsonValueKind.Object &&
                    progress.TryGetProperty("message", out JsonElement progressMessage) && progressMessage.ValueKind == JsonValueKind.String)
                {
                    message = progressMessage.GetString() ?? string.Empty;
                }
                WorkspaceDirectorDraftTextBox.Text = $"Job {status}: {message}";
                WorkspaceDirectorStatusText.Text = status switch
                {
                    "canceled" => "Director generation canceled. Saved direction is unchanged.",
                    "failed" => "Director generation failed. Review the details before retrying.",
                    _ => "The Director is still working. Review again for progress, or cancel the draft here."
                };
                UpdateDirectorWorkspaceAvailability();
                return;
            }

            _directorReviewedJobId = jobId;
            WorkspaceDirectorDraftTextBox.Text = response.GetProperty("result").GetProperty("document").ToString();
            WorkspaceDirectorStatusText.Text = "Draft loaded for review. Apply it only after checking the Story Bible and scene constraints.";
            UpdateDirectorWorkspaceAvailability();
            ShowStatus("Draft ready", "Review the draft, then apply it to the saved Workspace direction when approved.", InfoBarSeverity.Informational);
        });
    }

    private async void CancelWorkspaceDirectorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId || _directorDraftJobId is not string jobId ||
            jobId != _session.SelectedJobId || _directorDraftJobStatus is not ("queued" or "running" or "paused"))
        {
            return;
        }

        await RunBusyAsync("Canceling Director draft", async cancellationToken =>
        {
            StudioJobActionResponse response = await App.Services.ApiClient.CancelJobAsync(projectId, jobId, cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            if (_directorProjectId != projectId || _session.ActiveProjectId != projectId || _directorDraftJobId != jobId)
            {
                return;
            }
            _directorDraftJobStatus = response.Job.Status;
            _directorReviewedJobId = null;
            WorkspaceDirectorDraftTextBox.Text = $"Job {response.Job.Status}: {jobId}";
            WorkspaceDirectorStatusText.Text = response.Job.Status == "canceled"
                ? "Director generation canceled. Saved direction is unchanged. The worker is finishing cancellation."
                : $"The job is already {response.Job.Status}. Review the draft for its latest result.";
            UpdateDirectorWorkspaceAvailability();
        });
    }

    private async void ApplyWorkspaceDirectorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId || string.IsNullOrWhiteSpace(_directorReviewedJobId))
        {
            return;
        }

        await RunBusyAsync("Applying Director draft", async cancellationToken =>
        {
            JsonElement response = await App.Services.ApiClient.ApplyDirectorDraftAsync(
                projectId,
                _directorReviewedJobId!,
                new DirectorApplyRequest(_directorRevision),
                cancellationToken);
            ApplyDirectorWorkspaceDocument(response);
            WorkspaceDirectorDraftTextBox.Text = "Reviewed Director draft applied to the shared Workspace direction.";
            await RefreshProjectSnapshotAsync(projectId, cancellationToken);
            WorkspaceDirectorStatusText.Text = "Director direction applied. Planner, Timeline, Render, and both clients now see the same project revision.";
            ShowStatus("Director applied", "The reviewed draft is now the active project direction.", InfoBarSeverity.Success);
        });
    }

    private void OpenFullDirectorButton_Click(object sender, RoutedEventArgs e) => NavigateTo("directorLab");

    private void PopulateOptionalWorkspaceData()
    {
        PopulateAssets();
        PopulateHealth();
        PopulateLiveData();
    }

    private void PopulateAnalysis(ProjectDto project)
    {
        AnalysisBpmText.Text = project.Bpm?.ToString("0.#") ?? "—";
        AnalysisDurationText.Text = project.DurationSeconds is double duration
            ? FormatDuration(duration)
            : "—";
        AnalysisSectionCountText.Text = project.SectionCount.ToString();
        TranscriptStatusText.Text = project.TranscriptStatus;

        if (!TryGetMetadataObject(project.Meta, "analysis", out JsonElement analysis))
        {
            AnalysisSummaryText.Text = "Upload and analyze audio to populate the native workbench.";
            AnalysisTagsText.Text = "No analysis tags yet.";
            AnalysisSectionsList.ItemsSource = Array.Empty<WorkspaceAnalysisSectionItem>();
            AnalysisJsonTextBox.Text = "{}";
            return;
        }

        AnalysisSummaryText.Text = GetAnalysisSummary(analysis, project);
        AnalysisTagsText.Text = GetAnalysisTags(analysis);
        AnalysisSectionsList.ItemsSource = GetAnalysisSections(analysis);
        AnalysisJsonTextBox.Text = PrettyPrint(analysis);
    }

    private void PopulateAssets()
    {
        AssetItems.Clear();
        IEnumerable<WorkspaceAssetPathDto> audio = _assets?.Assets.Audio ?? [];
        IEnumerable<WorkspaceAssetPathDto> references = _assets?.Assets.References ?? [];
        var missingPaths = new HashSet<string>(
            _health?.Health.AssetIndex.Missing.Select(item => item.Path) ?? [],
            StringComparer.OrdinalIgnoreCase);

        foreach (WorkspaceAssetPathDto asset in audio)
        {
            AssetItems.Add(WorkspaceAssetItem.From("Audio", asset, missingPaths.Contains(asset.Path)));
        }

        foreach (WorkspaceAssetPathDto asset in references)
        {
            AssetItems.Add(WorkspaceAssetItem.From("Reference", asset, missingPaths.Contains(asset.Path)));
        }

        AssetEmptyText.Visibility = AssetItems.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        AssetsListView.Visibility = AssetItems.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
    }

    private void PopulatePlanning()
    {
        SynchronizeVariantSelection();
        VariantItems.Clear();
        for (int index = 0; index < CurrentVariants.Count; index++)
        {
            VariantItems.Add(WorkspaceVariantItem.From(index, CurrentVariants[index]));
        }

        _isSynchronizingSelection = true;
        VariantRadioButtons.ItemsSource = VariantItems;
        VariantRadioButtons.SelectedIndex = VariantItems.Count == 0 ? -1 : _session.SelectedVariantIndex;
        _isSynchronizingSelection = false;
        PopulateSelectedVariant();
    }

    private void PopulateSelectedVariant()
    {
        if (!TryGetSelectedVariant(out PlanVariantDto variant))
        {
            VariantJsonTextBox.Text = "{}";
            StoryboardItems.Clear();
            StoryboardSummaryText.Text = "Generate a plan to review and reorder storyboard scenes.";
            StoryboardEmptyText.Visibility = Visibility.Visible;
            StoryboardListView.Visibility = Visibility.Collapsed;
            return;
        }

        VariantJsonTextBox.Text = ToJson(variant);
        PopulateStoryboard();
    }

    private void PopulateStoryboard()
    {
        StoryboardItems.Clear();
        if (TryGetSelectedVariant(out PlanVariantDto variant))
        {
            for (int index = 0; index < variant.Scenes.Count; index++)
            {
                StoryboardItems.Add(
                    WorkspaceStoryboardItem.From(
                        index,
                        variant.Scenes[index],
                        index < variant.Scenes.Count - 1));
            }

            StoryboardSummaryText.Text =
                $"{variant.DisplayName}  •  {variant.SceneCount} scenes  •  " +
                $"{FormatDuration(variant.DurationSeconds ?? variant.Scenes.Sum(scene => Math.Max(0, scene.EndSeconds - scene.StartSeconds)))}";
        }
        else
        {
            StoryboardSummaryText.Text = "Generate a plan to review and reorder storyboard scenes.";
        }

        StoryboardEmptyText.Visibility = StoryboardItems.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        StoryboardListView.Visibility = StoryboardItems.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
    }

    private void PopulateHealth()
    {
        if (_health is null)
        {
            HealthStatusText.Text = "Health unavailable";
            HealthSummaryText.Text = "Run a health check when the backend is available.";
            MissingAssetsList.ItemsSource = Array.Empty<WorkspaceMissingAssetItem>();
            RelinkSuggestionsList.ItemsSource = Array.Empty<WorkspaceRelinkItem>();
            return;
        }

        ProjectHealthDto health = _health.Health;
        int missingCount = health.AssetIndex.Missing.Count;
        HealthStatusText.Text = health.Ok ? "Healthy" : "Needs attention";
        HealthSummaryText.Text =
            $"{health.AssetIndex.AssetCount} indexed assets; {missingCount} missing. " +
            $"{health.AssetIndex.TotalBytes / (1024d * 1024d):0.0} MB indexed.";
        MissingAssetsList.ItemsSource = health.AssetIndex.Missing
            .Select(WorkspaceMissingAssetItem.From)
            .ToList();
        RelinkSuggestionsList.ItemsSource = (_relinkSuggestions?.Suggestions ?? [])
            .Select(WorkspaceRelinkItem.From)
            .ToList();
        PopulateAssets();
    }

    private void PopulateLiveData()
    {
        MusicGraphResponse? graph = _musicGraph;
        if (graph is null)
        {
            MusicGraphSummaryText.Text = "Music Graph unavailable.";
            MusicGraphSectionsList.ItemsSource = Array.Empty<WorkspaceGraphSectionItem>();
        }
        else
        {
            MusicGraphSummaryText.Text =
                $"{graph.Tempo.Bpm:0.#} BPM  •  " +
                $"{graph.Beats.Count} beats  •  {graph.Stems.Count} stems  •  {graph.ConfidenceNotes.Count} confidence notes.";
            MusicGraphSectionsList.ItemsSource = graph.Sections
                .Select(WorkspaceGraphSectionItem.From)
                .ToList();
        }

        LiveCueSummaryText.Text = _liveCues is null
            ? "Live cue summary unavailable."
            : $"{_liveCues.EventCount} cue events are available for live performance.";
        LiveAssetSummaryText.Text = _liveAssets is null
            ? "Live asset summary unavailable."
            : $"{_liveAssets.PackCount} packs across {_liveAssets.ChannelCount} channels.";
    }

    private void PopulateMetadata(ProjectDto project)
    {
        VisualDnaText.Text = GetResponseMetadataSummary(
            _projectResponse?.VisualDna,
            "No Visual DNA has been saved.");
        CreativeDirectionText.Text = GetMetadataSummary(
            project.Meta,
            ["creative_direction", "director_mode", "conductor_intent"],
            "No creative direction metadata has been saved.");
        TimelinePreviewText.Text = GetMetadataSummary(
            project.Meta,
            ["timeline_preview", "timeline"],
            project.HasPlan
                ? $"{project.PlanVariants.Count} persisted plan variants are ready for Timeline."
                : "No timeline preview metadata is available.");
    }

    private void SynchronizeVariantSelection()
    {
        _session.SelectedVariantIndex = WorkspaceModelHelpers.ClampVariantIndex(
            _session.SelectedVariantIndex,
            CurrentVariants.Count);
    }

    private IReadOnlyList<PlanVariantDto> CurrentVariants =>
        _generatedPlan is { Variants.Count: > 0 }
            ? _generatedPlan.Variants
            : _projectResponse?.Project.PlanVariants ?? [];

    private bool TryGetSelectedVariant(out PlanVariantDto variant)
    {
        int index = _session.SelectedVariantIndex;
        if (index >= 0 && index < CurrentVariants.Count)
        {
            variant = CurrentVariants[index];
            return true;
        }

        variant = null!;
        return false;
    }

    private bool TryGetActiveProjectId(out string projectId)
    {
        if (!string.IsNullOrWhiteSpace(_session.ActiveProjectId))
        {
            projectId = _session.ActiveProjectId;
            return true;
        }

        projectId = string.Empty;
        ShowStatus("Project required", "Select a project before using this Workspace action.", InfoBarSeverity.Warning);
        return false;
    }

    private async Task RunBusyAsync(
        string operation,
        Func<CancellationToken, Task> action,
        CancellationToken cancellationToken = default)
    {
        CancelCurrentOperation();
        using var operationCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _operationCts = operationCts;
        WorkspaceProgressRing.IsActive = true;
        WorkspaceProgressRing.Visibility = Visibility.Visible;

        try
        {
            await action(operationCts.Token);
        }
        catch (OperationCanceledException) when (operationCts.IsCancellationRequested)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            if (!operationCts.IsCancellationRequested)
            {
                await HandleProjectRevisionConflictAsync(conflict, operationCts.Token);
            }
        }
        catch (StudioApiException exception)
        {
            if (!operationCts.IsCancellationRequested)
            {
                ShowStatus($"{operation} failed", exception.UserFacingMessage, InfoBarSeverity.Error);
            }
        }
        catch (Exception exception)
        {
            if (!operationCts.IsCancellationRequested)
            {
                ShowStatus($"{operation} failed", exception.Message, InfoBarSeverity.Error);
            }
        }
        finally
        {
            if (ReferenceEquals(_operationCts, operationCts))
            {
                _operationCts = null;
                WorkspaceProgressRing.IsActive = false;
                WorkspaceProgressRing.Visibility = Visibility.Collapsed;
            }
        }
    }

    private async Task HandleProjectRevisionConflictAsync(
        ProjectRevisionConflictException conflict,
        CancellationToken cancellationToken)
    {
        if (!await StudioPageHelpers.ConfirmReloadAfterRevisionConflictAsync(XamlRoot, conflict))
        {
            ShowStatus(
                "Project reload required",
                "The failed change was not applied. Review any local work, reload the project, then retry.",
                InfoBarSeverity.Warning);
            return;
        }

        if (!TryGetActiveProjectId(out string projectId))
        {
            return;
        }

        try
        {
            _generatedPlan = null;
            await LoadSelectedProjectAsync(projectId, cancellationToken);
            ShowStatus(
                "Project reloaded",
                "The latest revision is loaded. Review the current project, then retry your change.",
                InfoBarSeverity.Informational);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (StudioApiException reloadError)
        {
            ShowStatus("Reload failed", reloadError.UserFacingMessage, InfoBarSeverity.Error);
        }
        catch (HttpRequestException reloadError)
        {
            ShowStatus("Reload failed", reloadError.Message, InfoBarSeverity.Error);
        }
        catch (JsonException reloadError)
        {
            ShowStatus("Reload failed", reloadError.Message, InfoBarSeverity.Error);
        }
    }

    private void CancelCurrentOperation()
    {
        if (_operationCts is null)
        {
            return;
        }

        _operationCts.Cancel();
        _operationCts = null;
    }

    private static async Task<string?> PickFileAsync(string title, IReadOnlyList<string> extensions)
    {
        var picker = new FileOpenPicker(App.MainWindowInstance!.AppWindow.Id)
        {
            SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
            CommitButtonText = "Select",
            ViewMode = PickerViewMode.List,
        };
        foreach (string extension in extensions)
        {
            picker.FileTypeFilter.Add(extension);
        }

        PickFileResult result = await picker.PickSingleFileAsync();
        return result is null ? null : result.Path;
    }

    private static void EnsureSpecialistPage(Frame frame, Type pageType)
    {
        if (frame.Content?.GetType() != pageType)
        {
            frame.Navigate(pageType);
        }
    }

    private void SetWorkspaceMode(bool isStoryboard, bool isPlanner, bool isReactive)
    {
        OverviewPanel.Visibility = isStoryboard || isPlanner || isReactive ? Visibility.Collapsed : Visibility.Visible;
        StoryboardPanel.Visibility = isStoryboard ? Visibility.Visible : Visibility.Collapsed;
        PlannerPanel.Visibility = isPlanner ? Visibility.Visible : Visibility.Collapsed;
        ReactivePanel.Visibility = isReactive ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ClearWorkspace(string message)
    {
        _projectResponse = null;
        _assets = null;
        _health = null;
        _relinkSuggestions = null;
        _musicGraph = null;
        _liveCues = null;
        _liveAssets = null;
        _generatedPlan = null;
        _directorDocument = null;
        _directorProjectId = null;
        _directorReviewedJobId = null;
        _directorDraftJobId = null;
        _directorDraftJobStatus = null;
        _directorPendingRequest = null;
        _directorReadiness = null;
        _workflowDocument = null;
        _workflowSavedDocument = null;
        _workflowProjectId = null;
        _workflowDraftId = null;
        _workflowStatus = "not_prepared";
        _workflowRevision = 0;
        WorkflowSceneItems.Clear();
        WorkflowThemeTextBox.Text = string.Empty;
        WorkflowStyleTextBox.Text = string.Empty;
        WorkflowThemeTextBox.IsEnabled = false;
        WorkflowStyleTextBox.IsEnabled = false;
        WorkflowStatusText.Text = message;
        WorkflowSummaryText.Text = string.Empty;
        PrepareWorkflowButton.IsEnabled = false;
        SaveWorkflowButton.IsEnabled = false;
        ApplyWorkflowButton.IsEnabled = false;
        DiscardWorkflowEditsButton.IsEnabled = false;
        AssetItems.Clear();
        VariantItems.Clear();
        StoryboardItems.Clear();
        ProjectStatusText.Text = message;
        AnalysisSummaryText.Text = "No project analysis loaded.";
        AnalysisJsonTextBox.Text = "{}";
        VariantJsonTextBox.Text = "{}";
        LastOperationJsonTextBox.Text = "{}";
        PopulateOptionalWorkspaceData();
        PopulatePlanning();
        WorkspaceDirectorThemeTextBox.Text = string.Empty;
        WorkspaceDirectorStyleTextBox.Text = string.Empty;
        WorkspaceDirectorSceneSpecsTextBox.Text = "[]";
        WorkspaceDirectorPromptPreviewTextBox.Text = string.Empty;
        WorkspaceDirectorDraftTextBox.Text = string.Empty;
        WorkspaceDirectorReadinessText.Text = message;
        WorkspaceDirectorStatusText.Text = message;
        UpdateDirectorWorkspaceAvailability();
    }

    private void NavigateTo(string destination)
    {
        App.Navigate(destination);
    }

    private void ShowStatus(string title, string message, InfoBarSeverity severity)
    {
        StatusInfoBar.Title = title;
        StatusInfoBar.Message = message;
        StatusInfoBar.Severity = severity;
        StatusInfoBar.IsOpen = true;
    }

    private void SetLastOperationJson<T>(T value)
    {
        LastOperationJsonTextBox.Text = ToJson(value);
    }

    private void CopyTextToClipboard(string text, string successMessage)
    {
        var package = new DataPackage();
        package.SetText(text);
        Clipboard.SetContent(package);
        Clipboard.Flush();
        ShowStatus("Copied", successMessage, InfoBarSeverity.Success);
    }

    private string GetSelectedPlanMode()
    {
        if (PlanModeComboBox.SelectedItem is ComboBoxItem { Tag: string mode })
        {
            return mode;
        }

        return "auto";
    }

    private static bool TryGetMetadataObject(JsonElement metadata, string name, out JsonElement value)
    {
        value = default;
        return metadata.ValueKind == JsonValueKind.Object &&
               metadata.TryGetProperty(name, out value) &&
               value.ValueKind == JsonValueKind.Object;
    }

    private static string GetAnalysisSummary(JsonElement analysis, ProjectDto project)
    {
        string transcript = project.TranscriptStatus;
        string bpm = project.Bpm is double value ? $"{value:0.#} BPM" : "tempo unavailable";
        string duration = project.DurationSeconds is double seconds ? FormatDuration(seconds) : "duration unavailable";
        return $"{bpm}; {duration}; {project.SectionCount} sections. {transcript}.";
    }

    private static string GetAnalysisTags(JsonElement analysis)
    {
        foreach (string propertyName in new[] { "tags", "semantic_tags" })
        {
            if (!analysis.TryGetProperty(propertyName, out JsonElement tags))
            {
                continue;
            }

            if (tags.ValueKind == JsonValueKind.Array)
            {
                string[] values = tags.EnumerateArray()
                    .Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.ToString())
                    .Where(value => !string.IsNullOrWhiteSpace(value))
                    .Select(value => value!)
                    .ToArray();
                if (values.Length > 0)
                {
                    return string.Join("  •  ", values);
                }
            }
        }

        return "No semantic tags reported.";
    }

    private static IReadOnlyList<WorkspaceAnalysisSectionItem> GetAnalysisSections(JsonElement analysis)
    {
        if (!analysis.TryGetProperty("sections", out JsonElement sections) ||
            sections.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        var result = new List<WorkspaceAnalysisSectionItem>();
        int index = 0;
        foreach (JsonElement section in sections.EnumerateArray())
        {
            double start = ReadDouble(section, "start_s") ?? ReadDouble(section, "start") ?? 0;
            double end = ReadDouble(section, "end_s") ?? ReadDouble(section, "end") ?? start;
            string label = ReadString(section, "label") ?? ReadString(section, "name") ?? $"Section {index + 1}";
            result.Add(new WorkspaceAnalysisSectionItem(label, start, end));
            index++;
        }

        return result;
    }

    private static string GetResponseMetadataSummary(JsonElement? element, string fallback)
    {
        if (element is not { ValueKind: JsonValueKind.Object } value || !value.EnumerateObject().Any())
        {
            return fallback;
        }

        return SummarizeObject(value);
    }

    private static string GetMetadataSummary(
        JsonElement metadata,
        IEnumerable<string> keys,
        string fallback)
    {
        if (metadata.ValueKind != JsonValueKind.Object)
        {
            return fallback;
        }

        foreach (string key in keys)
        {
            if (!metadata.TryGetProperty(key, out JsonElement value) ||
                value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
            {
                continue;
            }

            return value.ValueKind == JsonValueKind.Object ? SummarizeObject(value) : value.ToString();
        }

        return fallback;
    }

    private static string SummarizeObject(JsonElement value)
    {
        string[] fields = value.EnumerateObject()
            .Take(4)
            .Select(property => $"{property.Name.Replace('_', ' ')}: {SummarizeValue(property.Value)}")
            .ToArray();
        return fields.Length == 0 ? "No metadata values were reported." : string.Join("  •  ", fields);
    }

    private static string SummarizeValue(JsonElement value) =>
        value.ValueKind switch
        {
            JsonValueKind.Array => $"{value.GetArrayLength()} items",
            JsonValueKind.Object => $"{value.EnumerateObject().Count()} fields",
            JsonValueKind.String => value.GetString() ?? string.Empty,
            _ => value.ToString(),
        };

    private static double? ReadDouble(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out JsonElement value) &&
        value.TryGetDouble(out double result)
            ? result
            : null;

    private static string? ReadString(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out JsonElement value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string ToJson<T>(T value) =>
        PrettyPrint(JsonSerializer.SerializeToElement(value, StudioJson.GetTypeInfo<T>()));

    private static string PrettyPrint(JsonElement value)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Indented = true }))
        {
            value.WriteTo(writer);
        }

        return System.Text.Encoding.UTF8.GetString(stream.ToArray());
    }

    private static string FormatDuration(double seconds)
    {
        TimeSpan duration = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return duration.TotalHours >= 1
            ? duration.ToString(@"h\:mm\:ss")
            : duration.ToString(@"m\:ss");
    }

    private static string? NullIfWhiteSpace(string value) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim();

    private static string GetAudioContentType(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".wav" => "audio/wav",
            ".mp3" => "audio/mpeg",
            ".flac" => "audio/flac",
            ".m4a" => "audio/mp4",
            ".ogg" => "audio/ogg",
            ".aac" => "audio/aac",
            _ => "application/octet-stream",
        };

    private static string GetImageContentType(string path) =>
        Path.GetExtension(path).ToLowerInvariant() switch
        {
            ".png" => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".webp" => "image/webp",
            ".bmp" => "image/bmp",
            _ => "application/octet-stream",
        };
}

public sealed record WorkspaceAnalysisSectionItem(string Label, double StartSeconds, double EndSeconds)
{
    public string TimeRange => $"{StartSeconds:0.0}s – {EndSeconds:0.0}s";
}

public sealed class WorkspaceAssetItem
{
    public WorkspaceAssetItem(string role, string displayName, string path, string detail, string availability)
    {
        Role = role;
        DisplayName = displayName;
        Path = path;
        Detail = detail;
        Availability = availability;
    }

    public string Role { get; set; }

    public string DisplayName { get; set; }

    public string Path { get; set; }

    public string Detail { get; set; }

    public string Availability { get; set; }

    public bool CanOpen => File.Exists(Path);

    public string OpenAutomationId => $"Workspace.Asset.Open.{DisplayName}";

    public static WorkspaceAssetItem From(string kind, WorkspaceAssetPathDto asset, bool isMissing)
    {
        return new WorkspaceAssetItem(
            kind,
            System.IO.Path.GetFileName(asset.Path),
            asset.Path,
            "Project asset",
            isMissing ? "Missing" : "Indexed");
    }
}

public sealed class WorkspaceVariantItem
{
    public WorkspaceVariantItem(int index, string displayName, string logline, string detail)
    {
        Index = index;
        DisplayName = displayName;
        Logline = logline;
        Detail = detail;
    }

    public int Index { get; set; }

    public string DisplayName { get; set; }

    public string Logline { get; set; }

    public string Detail { get; set; }

    public string Summary => $"{Logline}  •  {Detail}";

    public static WorkspaceVariantItem From(int index, PlanVariantDto variant) =>
        new WorkspaceVariantItem(
            index,
            variant.DisplayName,
            string.IsNullOrWhiteSpace(variant.Logline) ? "No logline supplied." : variant.Logline,
            $"{variant.SceneCount} scenes  •  {(variant.DurationSeconds is double duration ? $"{duration:0.0}s" : "duration follows scenes")}");
}

public sealed class WorkspaceStoryboardItem
{
    public WorkspaceStoryboardItem(
        int index,
        int sceneNumber,
        string timeRange,
        string prompt,
        string negativePrompt)
    {
        Index = index;
        SceneNumber = sceneNumber;
        TimeRange = timeRange;
        Prompt = prompt;
        NegativePrompt = negativePrompt;
    }

    public int Index { get; set; }

    public int SceneNumber { get; set; }

    public string TimeRange { get; set; }

    public string Prompt { get; set; }

    public string NegativePrompt { get; set; }

    public static WorkspaceStoryboardItem From(int index, PlanSceneDto scene, bool canMoveLater) =>
        new WorkspaceStoryboardItem(
            index,
            index + 1,
            $"{scene.StartSeconds:0.0}s – {scene.EndSeconds:0.0}s",
            scene.Prompt,
            string.IsNullOrWhiteSpace(scene.NegativePrompt)
                ? "No negative prompt."
                : $"Avoid: {scene.NegativePrompt}")
        {
            CanMoveLater = canMoveLater,
        };

    public string PositionText => SceneNumber.ToString(CultureInfo.InvariantCulture);

    public string TimingText => TimeRange;

    public bool CanMoveEarlier => Index > 0;

    public bool CanMoveLater { get; set; }

    public string MoveEarlierAutomationId => $"Workspace.Storyboard.Scene.{Index}.MoveEarlier";

    public string MoveLaterAutomationId => $"Workspace.Storyboard.Scene.{Index}.MoveLater";
}

public sealed record WorkspaceMissingAssetItem(string Name, string Path, string Kind)
{
    public static WorkspaceMissingAssetItem From(ProjectMissingAssetDto asset) =>
        new(System.IO.Path.GetFileName(asset.Path), asset.Path, asset.Reason);
}

public sealed record WorkspaceRelinkItem(string MissingName, string Candidate, string Score)
{
    public static WorkspaceRelinkItem From(ProjectRelinkSuggestionDto suggestion) =>
        new(
            System.IO.Path.GetFileName(suggestion.Missing),
            string.IsNullOrWhiteSpace(suggestion.Candidate) ? "No candidate found" : suggestion.Candidate,
            "Suggested path");
}

public sealed record WorkspaceGraphSectionItem(string Label, string TimeRange, string Detail)
{
    public static WorkspaceGraphSectionItem From(MusicGraphSectionDto section) =>
        new(
            string.IsNullOrWhiteSpace(section.Label) ? "Unlabelled section" : section.Label,
            $"{section.Start:0.0}s – {section.End:0.0}s",
            $"{Math.Max(0, section.End - section.Start):0.0}s");
}
