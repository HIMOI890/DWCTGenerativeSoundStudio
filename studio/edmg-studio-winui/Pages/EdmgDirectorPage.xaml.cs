using EdmgStudio.Core.Models;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class EdmgDirectorPage : Page, IStudioRefreshable
{
    private CancellationTokenSource? _loadCancellation;
    private bool _isSynchronizingSelection;
    private string _nextDestination = "projects";
    private JsonObject? _directorDocument;
    private long _directorRevision;
    private string? _directorProjectId;
    private string? _reviewedDirectorJobId;
    private DirectorGenerationRequest? _pendingDirectorRequest;

    public EdmgDirectorPage()
    {
        InitializeComponent();
        Loaded += EdmgDirectorPage_Loaded;
        Unloaded += EdmgDirectorPage_Unloaded;
    }

    private async void EdmgDirectorPage_Loaded(object sender, RoutedEventArgs e)
    {
        await RefreshAsync();
    }

    private void EdmgDirectorPage_Unloaded(object sender, RoutedEventArgs e)
    {
        _loadCancellation?.Cancel();
        _loadCancellation?.Dispose();
        _loadCancellation = null;
    }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        _loadCancellation?.Cancel();
        _loadCancellation?.Dispose();
        _loadCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        CancellationToken loadToken = _loadCancellation.Token;

        SetBusy(true);
        StatusInfoBar.IsOpen = false;
        try
        {
            ProjectListResponse response = await App.Services.ApiClient.GetProjectsAsync(loadToken);
            IReadOnlyList<ProjectDto> projects = response.Projects
                .OrderByDescending(project => project.CreatedAt, StringComparer.OrdinalIgnoreCase)
                .ToList();

            _isSynchronizingSelection = true;
            ProjectComboBox.ItemsSource = projects;
            string? projectId = App.Services.Session.ActiveProjectId;
            ProjectComboBox.SelectedItem = projects.FirstOrDefault(project =>
                string.Equals(project.Id, projectId, StringComparison.Ordinal));
            _isSynchronizingSelection = false;

            if (ProjectComboBox.SelectedItem is ProjectDto project)
            {
                await LoadProjectAsync(project.Id, loadToken);
            }
            else
            {
                ClearProjectSummary();
                ShowStatus(
                    projects.Count == 0
                        ? "Create a project before directing a production."
                        : "Choose an active project to share its context across the Studio workflow.",
                    InfoBarSeverity.Warning);
            }
        }
        catch (OperationCanceledException) when (loadToken.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            ClearProjectSummary();
            ShowStatus(StudioPageHelpers.GetErrorMessage(exception), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
            UpdateProjectActionAvailability();
        }
    }

    private async Task LoadProjectAsync(string projectId, CancellationToken cancellationToken)
    {
        ProjectResponse response = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        ProjectDto project = response.Project;

        JsonElement director = await App.Services.ApiClient.GetDirectorDocumentAsync(projectId, cancellationToken);
        cancellationToken.ThrowIfCancellationRequested();
        _directorProjectId = projectId;
        _reviewedDirectorJobId = null;
        _pendingDirectorRequest = null;
        DirectorDraftBox.Text = "";
        ApplyDirectorDocument(director);

        ProjectIdText.Text = project.Id;
        AnalysisText.Text = project.HasAnalysis
            ? $"Ready · {project.SectionCount} sections"
            : project.HasAudio ? "Audio loaded; analysis pending" : "Audio required";
        PlanText.Text = project.HasPlan
            ? $"{project.PlanVariants.Count} plan variant{(project.PlanVariants.Count == 1 ? string.Empty : "s")} ready"
            : "Generate a creative plan";

        PopulateVariants(project.PlanVariants);
        UpdateSessionContext();
        ConfigureNextStep(project);

        try
        {
            ProjectHealthResponse healthResponse =
                await App.Services.ApiClient.GetProjectHealthAsync(projectId, cancellationToken);
            ProjectHealthDto health = healthResponse.Health;
            HealthText.Text =
                $"{(string.IsNullOrWhiteSpace(health.Status) ? (health.Ok ? "Ready" : "Attention required") : health.Status)}" +
                $" · {health.AssetIndex.AssetCount} assets · {health.AssetIndex.MissingCount} missing" +
                (health.Issues.Count == 0 ? string.Empty : $" · {health.Issues.Count} issues");
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception exception)
        {
            HealthText.Text = $"Health check unavailable: {StudioPageHelpers.GetErrorMessage(exception)}";
        }
    }

    private async void RefreshProject_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async void ProjectComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isSynchronizingSelection || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            return;
        }

        App.Services.Session.ActiveProjectId = project.Id;
        await RefreshAsync();
    }

    private void VariantComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isSynchronizingSelection || VariantComboBox.SelectedItem is not DirectorVariantOption variant)
        {
            return;
        }

        App.Services.Session.SelectedVariantIndex = variant.Index;
        UpdateSessionContext();
        ShowStatus($"{variant.Label} is now active across the Studio workflow.", InfoBarSeverity.Success);
    }

    private void PopulateVariants(IReadOnlyList<PlanVariantDto> variants)
    {
        IReadOnlyList<DirectorVariantOption> options = variants
            .Select((variant, index) => new DirectorVariantOption(
                index,
                string.IsNullOrWhiteSpace(variant.Name) ? $"Variant {index + 1}" : variant.Name!))
            .ToList();

        _isSynchronizingSelection = true;
        VariantComboBox.ItemsSource = options;
        if (options.Count > 0)
        {
            int index = Math.Clamp(App.Services.Session.SelectedVariantIndex, 0, options.Count - 1);
            App.Services.Session.SelectedVariantIndex = index;
            VariantComboBox.SelectedIndex = index;
        }
        else
        {
            App.Services.Session.SelectedVariantIndex = 0;
            VariantComboBox.SelectedIndex = -1;
        }

        _isSynchronizingSelection = false;
        VariantComboBox.IsEnabled = options.Count > 0;
    }

    private void ConfigureNextStep(ProjectDto project)
    {
        if (!project.HasAudio)
        {
            _nextDestination = "workspace";
            NextStepText.Text = "Add a source track or reference asset before developing direction.";
            NextStepButton.Content = "Open Workspace";
        }
        else if (!project.HasAnalysis)
        {
            _nextDestination = "workspace";
            NextStepText.Text = "Analyze the source track so planning and reactive tools can use its structure.";
            NextStepButton.Content = "Analyze in Workspace";
        }
        else if (!project.HasPlan)
        {
            _nextDestination = "plannerLab";
            NextStepText.Text = "Set conductor intent and director presets, then generate creative plan variants.";
            NextStepButton.Content = "Open AI Planner Lab";
        }
        else if (!string.IsNullOrWhiteSpace(App.Services.Session.SelectedJobId))
        {
            _nextDestination = "queue";
            NextStepText.Text = "A queue job is selected. Inspect progress or continue its production handoff.";
            NextStepButton.Content = "Open Queue";
        }
        else if (!string.IsNullOrWhiteSpace(App.Services.Session.SelectedArtifactPath))
        {
            _nextDestination = "review";
            NextStepText.Text = "An output is selected. Review the artifact and record editorial decisions.";
            NextStepButton.Content = "Open Review";
        }
        else
        {
            _nextDestination = "timeline";
            NextStepText.Text = "Direction is ready. Assemble the selected variant on the timeline before rendering.";
            NextStepButton.Content = "Open Timeline";
        }

        NextStepButton.IsEnabled = true;
    }

    private void ClearProjectSummary()
    {
        _directorDocument = null;
        _directorProjectId = null;
        StoryThemeBox.Text = "";
        StoryStyleBox.Text = "";
        SceneSpecsBox.Text = "[]";
        PromptPreviewBox.Text = "";
        _isSynchronizingSelection = true;
        VariantComboBox.ItemsSource = null;
        VariantComboBox.SelectedIndex = -1;
        _isSynchronizingSelection = false;

        ProjectIdText.Text = "—";
        AnalysisText.Text = "Unavailable";
        PlanText.Text = "Unavailable";
        HealthText.Text = "Not checked";
        VariantComboBox.IsEnabled = false;
        _nextDestination = "projects";
        NextStepText.Text = "Choose or create a project to begin.";
        NextStepButton.Content = "Open Projects";
        NextStepButton.IsEnabled = true;
        UpdateSessionContext();
    }

    private void UpdateSessionContext()
    {
        SourceAssetText.Text = DisplayPath(App.Services.Session.SourceAssetPath, "None selected");
        ArtifactText.Text = DisplayPath(App.Services.Session.SelectedArtifactPath, "None selected");
        JobText.Text = App.Services.Session.SelectedJobId ?? "None selected";
        TimelineFocusText.Text = App.Services.Session.TimelineFocusSeconds is double focus
            ? $"{focus:0.###} seconds"
            : "No focused time";
        RenderContextText.Text = DisplayPath(App.Services.Session.RenderContext, "Default");
    }

    private void SetBusy(bool isBusy)
    {
        BusyIndicator.Visibility = isBusy ? Visibility.Visible : Visibility.Collapsed;
        StudioPageHelpers.SetControlsEnabled(this, !isBusy);
    }

    private void UpdateProjectActionAvailability()
    {
        bool hasProject = !string.IsNullOrWhiteSpace(App.Services.Session.ActiveProjectId);
        ProductionActions.IsEnabled = hasProject;
        ApplyDirectorDraftButton.IsEnabled = hasProject && _reviewedDirectorJobId is not null;
        VariantComboBox.IsEnabled = hasProject && VariantComboBox.Items.Count > 0;
        NextStepButton.IsEnabled = true;
    }

    private void ApplyDirectorDocument(JsonElement response)
    {
        _directorRevision = response.GetProperty("revision").GetInt64();
        _directorDocument = JsonNode.Parse(response.GetProperty("document").GetRawText())!.AsObject();
        StoryThemeBox.Text = _directorDocument["story_bible"]?["project_theme"]?.GetValue<string>() ?? "";
        StoryStyleBox.Text = _directorDocument["story_bible"]?["visual_style"]?.GetValue<string>() ?? "";
        SceneSpecsBox.Text = _directorDocument["scenes"]?.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) ?? "[]";
        PromptPreviewBox.Text = "";
        _reviewedDirectorJobId = null;
        ApplyDirectorDraftButton.IsEnabled = false;
    }

    private async Task RunDirectorActionAsync(Func<string, CancellationToken, Task> action)
    {
        if (_directorProjectId is not string projectId) return;
        CancellationToken token = _loadCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            if (_directorDocument is null ||
                StoryThemeBox.Text != (_directorDocument["story_bible"]?["project_theme"]?.GetValue<string>() ?? "") ||
                StoryStyleBox.Text != (_directorDocument["story_bible"]?["visual_style"]?.GetValue<string>() ?? "") ||
                !JsonNode.DeepEquals(JsonNode.Parse(SceneSpecsBox.Text), _directorDocument["scenes"]))
                throw new InvalidOperationException("Save your direction changes before generating, reviewing or applying a draft.");
            await action(projectId, token);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception exception)
        {
            if (!token.IsCancellationRequested)
                ShowStatus(StudioPageHelpers.GetErrorMessage(exception), InfoBarSeverity.Error);
        }
        finally
        {
            if (!token.IsCancellationRequested)
            {
                SetBusy(false);
                UpdateProjectActionAvailability();
                ApplyDirectorDraftButton.IsEnabled = _reviewedDirectorJobId is not null;
            }
        }
    }

    private async void GenerateDirector_Click(object sender, RoutedEventArgs e) =>
        await RunDirectorActionAsync(async (projectId, token) =>
        {
            string instruction = DirectorInstructionBox.Text.Trim();
            if (instruction.Length == 0) throw new InvalidOperationException("Enter direction for Qwen3-VL first.");
            string rendererEngine = (PromptEngineBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "hunyuan_video15";
            if (_pendingDirectorRequest is null ||
                _pendingDirectorRequest.Instruction != instruction ||
                _pendingDirectorRequest.ExpectedRevision != _directorRevision ||
                !string.Equals(_pendingDirectorRequest.RendererEngine, rendererEngine, StringComparison.Ordinal))
            {
                _pendingDirectorRequest = new(
                    _directorRevision,
                    Guid.NewGuid().ToString(),
                    instruction,
                    "automatic",
                    rendererEngine,
                    false);
            }
            JsonElement response = await App.Services.ApiClient.GenerateDirectorAsync(projectId, _pendingDirectorRequest, token);
            token.ThrowIfCancellationRequested();
            App.Services.Session.SetSelectedJob(projectId, response.GetProperty("job_id").GetString());
            _pendingDirectorRequest = null;
            _reviewedDirectorJobId = null;
            DirectorDraftBox.Text = "Draft queued. Review the selected job when generation finishes.";
            UpdateSessionContext();
            ShowStatus("Director draft queued. Track progress or cancel in Queue.", InfoBarSeverity.Success);
        });

    private async void ReviewDirector_Click(object sender, RoutedEventArgs e) =>
        await RunDirectorActionAsync(async (projectId, token) =>
        {
            _reviewedDirectorJobId = null;
            string jobId = App.Services.Session.SelectedJobId ?? throw new InvalidOperationException("Select a Director job in Queue first.");
            JsonElement draft = await App.Services.ApiClient.GetDirectorDraftAsync(projectId, jobId, token);
            token.ThrowIfCancellationRequested();
            string? status = draft.GetProperty("status").GetString();
            if (status != "succeeded")
            {
                DirectorDraftBox.Text = $"Job {status}: {draft.GetProperty("error")}";
                return;
            }
            DirectorDraftBox.Text = JsonNode.Parse(draft.GetProperty("result").GetProperty("document").GetRawText())!.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
            _reviewedDirectorJobId = jobId;
            ShowStatus("Review the draft before applying it to saved direction.", InfoBarSeverity.Informational);
        });

    private async void ApplyDirectorDraft_Click(object sender, RoutedEventArgs e) =>
        await RunDirectorActionAsync(async (projectId, token) =>
        {
            if (_reviewedDirectorJobId is not string jobId) return;
            JsonElement response = await App.Services.ApiClient.ApplyDirectorDraftAsync(projectId, jobId, new(_directorRevision), token);
            token.ThrowIfCancellationRequested();
            ApplyDirectorDocument(response);
            DirectorDraftBox.Text = "Reviewed draft applied.";
            ShowStatus("Reviewed direction applied to the project.", InfoBarSeverity.Success);
        });

    private async void SaveDirector_Click(object sender, RoutedEventArgs e)
    {
        if (_directorDocument is null || _directorProjectId is not string projectId) return;
        CancellationToken token = _loadCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            JsonObject document = _directorDocument.DeepClone().AsObject();
            document["story_bible"]!["project_theme"] = StoryThemeBox.Text;
            document["story_bible"]!["visual_style"] = StoryStyleBox.Text;
            document["scenes"] = JsonNode.Parse(SceneSpecsBox.Text)?.AsArray() ?? throw new JsonException("Scenes must be a JSON array.");
            using JsonDocument payload = JsonDocument.Parse(document.ToJsonString());
            JsonElement saved = await App.Services.ApiClient.SaveDirectorDocumentAsync(projectId, new(_directorRevision, payload.RootElement.Clone()), token);
            token.ThrowIfCancellationRequested();
            if (_directorProjectId != projectId) return;
            ApplyDirectorDocument(saved);
            ShowStatus("Story Bible and scenes saved.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception exception) { ShowStatus(StudioPageHelpers.GetErrorMessage(exception), InfoBarSeverity.Error); }
        finally { if (!token.IsCancellationRequested) { SetBusy(false); UpdateProjectActionAvailability(); } }
    }

    private async void CompileDirector_Click(object sender, RoutedEventArgs e)
    {
        if (_directorProjectId is not string projectId) return;
        CancellationToken token = _loadCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            string engine = (PromptEngineBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "hunyuan_video15";
            JsonElement response = await App.Services.ApiClient.GetDirectorPromptsAsync(projectId, engine, token);
            token.ThrowIfCancellationRequested();
            if (_directorProjectId != projectId) return;
            PromptPreviewBox.Text = string.Join("\n\n", response.GetProperty("packages").EnumerateArray().Select(item => item.GetProperty("prompt").GetString()));
            ShowStatus("Saved scenes compiled. No generation job submitted.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { }
        catch (Exception exception) { ShowStatus(StudioPageHelpers.GetErrorMessage(exception), InfoBarSeverity.Error); }
        finally { if (!token.IsCancellationRequested) { SetBusy(false); UpdateProjectActionAvailability(); } }
    }

    private void ShowStatus(string message, InfoBarSeverity severity)
    {
        StatusInfoBar.Title = severity switch
        {
            InfoBarSeverity.Error => "Director unavailable",
            InfoBarSeverity.Success => "Director updated",
            _ => "Director",
        };
        StatusInfoBar.Message = message;
        StatusInfoBar.Severity = severity;
        StatusInfoBar.IsOpen = true;
    }

    private static string DisplayPath(string? value, string fallback)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return fallback;
        }

        string fileName = Path.GetFileName(value);
        return string.IsNullOrWhiteSpace(fileName) ? value : fileName;
    }

    private void NextStepButton_Click(object sender, RoutedEventArgs e) => App.Navigate(_nextDestination);
    private void OpenPlanner_Click(object sender, RoutedEventArgs e) => App.Navigate("plannerLab");
    private void OpenReactive_Click(object sender, RoutedEventArgs e) => App.Navigate("reactiveLab");
    private void OpenWorkspace_Click(object sender, RoutedEventArgs e) => App.Navigate("workspace");
    private void OpenTimeline_Click(object sender, RoutedEventArgs e) => App.Navigate("timeline");
    private void OpenRender_Click(object sender, RoutedEventArgs e) => App.Navigate("render");
    private void OpenQueue_Click(object sender, RoutedEventArgs e) => App.Navigate("queue");
    private void OpenOutputs_Click(object sender, RoutedEventArgs e) => App.Navigate("outputs");
    private void OpenReview_Click(object sender, RoutedEventArgs e) => App.Navigate("review");
    private void OpenForge_Click(object sender, RoutedEventArgs e) => App.Navigate("studioForge");

    private sealed record DirectorVariantOption(int Index, string Label);
}
