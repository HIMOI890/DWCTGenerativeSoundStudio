using System.Text.Json;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using EdmgStudio.WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.Storage.Pickers;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class AiPlannerLabPage : Page
{
    private static readonly StudioJsonContext _indentedJsonContext =
        new(new JsonSerializerOptions { WriteIndented = true });
    private readonly StudioSessionService _session = App.Services.Session;
    private CancellationTokenSource? _operationCancellation;
    private List<ProjectDto> _projects = [];
    private ProjectDto? _project;
    private PlanDto? _plan;
    private int _selectedVariantIndex = -1;
    private int _selectedSceneIndex = -1;
    private bool _isLoadingProject;
    private bool _isOperationBusy;
    private bool _isVariantDirty;
    private bool _suppressSceneEditorChanges;
    private bool _suppressSceneSelection;

    private PlanVariantDto? SelectedVariant =>
        _plan is not null &&
        _selectedVariantIndex >= 0 &&
        _selectedVariantIndex < _plan.Variants.Count
            ? _plan.Variants[_selectedVariantIndex]
            : null;

    private PlanSceneDto? SelectedScene =>
        SelectedVariant is { } variant &&
        _selectedSceneIndex >= 0 &&
        _selectedSceneIndex < variant.Scenes.Count
            ? variant.Scenes[_selectedSceneIndex]
            : null;

    public AiPlannerLabPage()
    {
        InitializeComponent();
        Loaded += AiPlannerLabPage_Loaded;
        Unloaded += AiPlannerLabPage_Unloaded;
    }

    private async void AiPlannerLabPage_Loaded(object sender, RoutedEventArgs e)
    {
        await LoadAsync();
    }

    private void AiPlannerLabPage_Unloaded(object sender, RoutedEventArgs e)
    {
        _operationCancellation?.Cancel();
    }

    private async Task LoadAsync()
    {
        await RunOperationAsync(
            "Loading Planner context",
            async cancellationToken =>
            {
                var projectsResponse = await App.Services.ApiClient.GetProjectsAsync(cancellationToken);
                _projects = projectsResponse.Projects;
                ProjectComboBox.ItemsSource = _projects;

                var activeProjectId = _session.ActiveProjectId;
                var selectedProject = _projects.FirstOrDefault(project => project.Id == activeProjectId)
                                      ?? _projects.FirstOrDefault();
                if (selectedProject is null)
                {
                    ClearPlan();
                    ShowStatus(InfoBarSeverity.Warning, "No project", "Create a project in Workspace before using Planner.");
                    return;
                }

                _isLoadingProject = true;
                ProjectComboBox.SelectedItem = selectedProject;
                _isLoadingProject = false;
                await LoadProjectAsync(selectedProject.Id, cancellationToken);
                await LoadAiReadinessAsync(cancellationToken);
                ShowStatus(InfoBarSeverity.Success, "Planner ready", $"Loaded {selectedProject.Name}.");
            });
    }

    private async Task LoadProjectAsync(string projectId, CancellationToken cancellationToken)
    {
        var response = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        _project = response.Project;
        _session.ActiveProjectId = response.Project.Id;
        if (response.VisualDna.ValueKind == JsonValueKind.Object)
        {
            VisualDnaTextBox.Text = FormatJson(response.VisualDna);
        }

        if (response.Project.HasPlan &&
            response.Project.Meta.TryGetProperty("last_plan", out var planJson) &&
            planJson.ValueKind == JsonValueKind.Object)
        {
            _plan = JsonSerializer.Deserialize(planJson.GetRawText(), StudioJson.GetTypeInfo<PlanDto>());
            PresentPlan();
        }
        else
        {
            ClearPlan();
        }
    }

    private async Task LoadAiReadinessAsync(CancellationToken cancellationToken)
    {
        var response = await App.Services.ApiClient.GetAiReadinessAsync(cancellationToken);
        var configuration = response.AiConfiguration;
        var readiness = configuration.IsReady switch
        {
            true => "ready",
            false => "not ready",
            null => "status unknown",
        };
        var provider = string.IsNullOrWhiteSpace(configuration.Label)
            ? configuration.Provider
            : configuration.Label;
        var model = string.IsNullOrWhiteSpace(configuration.Model) ? string.Empty : $" · {configuration.Model}";
        ProviderStatusText.Text = $"{provider} · {readiness}{model}" +
                                  (string.IsNullOrWhiteSpace(configuration.Warning)
                                      ? string.Empty
                                      : $"\n{configuration.Warning}");
    }

    private async void ProjectComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isLoadingProject || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            return;
        }

        if (!string.Equals(project.Id, _session.ActiveProjectId, StringComparison.Ordinal) &&
            !CanReplacePlan("switching projects"))
        {
            RestoreActiveProjectSelection();
            return;
        }

        await RunOperationAsync(
            "Switching project",
            cancellationToken => LoadProjectAsync(project.Id, cancellationToken),
            successMessage: $"Planner is now using {project.Name}.");
    }

    private async void RefreshPlannerButton_Click(object sender, RoutedEventArgs e)
    {
        if (!CanReplacePlan("refreshing Planner"))
        {
            return;
        }

        await LoadAsync();
    }

    private async void GeneratePlanButton_Click(object sender, RoutedEventArgs e)
    {
        if (!CanReplacePlan("generating a new plan"))
        {
            return;
        }

        if (ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            ShowStatus(InfoBarSeverity.Warning, "Select a project", "Planner needs an active project.");
            return;
        }

        var request = BuildPlanRequest();
        var errors = PlannerWorkflow.Validate(request);
        if (errors.Count > 0)
        {
            ShowStatus(InfoBarSeverity.Warning, "Review Planner settings", string.Join(Environment.NewLine, errors));
            return;
        }

        var mode = (PlanningModeComboBox.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "auto";
        await RunOperationAsync(
            "Generating plan variants",
            async cancellationToken =>
            {
                _plan = await App.Services.ApiClient.GeneratePlanAsync(project.Id, request, mode, cancellationToken);
                PresentPlan();
                await RefreshProjectRevisionAsync(project.Id, cancellationToken);
            },
            successMessage: $"Generated {_plan?.Variants.Count ?? 0} plan variants.");
    }

    private PlanRequest BuildPlanRequest()
    {
        var creativeSettings = new PlannerCreativeSettings(
            CreativeBriefTextBox.Text,
            VisualDnaTextBox.Text,
            ConstraintsTextBox.Text,
            PromptSeedTextBox.Text,
            DirectorPresetComboBox.Text,
            MotionPresetComboBox.Text,
            AnimationPresetComboBox.Text,
            RenderPresetComboBox.Text,
            ConductorIntentTextBox.Text);

        return new PlanRequest(
            NullIfWhiteSpace(TitleTextBox.Text),
            NullIfWhiteSpace(CreativeBriefTextBox.Text),
            NullIfWhiteSpace(PlannerWorkflow.BuildStylePreferences(creativeSettings)),
            (int)VariantCountNumberBox.Value,
            (int)SceneCountNumberBox.Value,
            StudioPageHelpers.ExpectedRevision(_project));
    }

    private void PresentPlan()
    {
        _isVariantDirty = false;
        var items = _plan?.Variants
            .Select((variant, index) => new PlannerVariantItem(variant, index))
            .ToList() ?? [];
        VariantListView.ItemsSource = items;
        RawPlanTextBox.Text = _plan is null
            ? string.Empty
            : JsonSerializer.Serialize(_plan, StudioJson.GetTypeInfo<PlanDto>());

        if (items.Count == 0)
        {
            _selectedVariantIndex = -1;
            _selectedSceneIndex = -1;
            SceneListView.ItemsSource = null;
            VariantTitleText.Text = "No variants";
            VariantLoglineText.Text = "Generate or import a plan to begin.";
            ClearSceneEditor();
            return;
        }

        var preferredIndex = Math.Clamp(_session.SelectedVariantIndex, 0, items.Count - 1);
        VariantListView.SelectedIndex = preferredIndex;
        SelectVariant(items[preferredIndex]);
    }

    private void VariantListView_ItemClick(object sender, ItemClickEventArgs e)
    {
        if (e.ClickedItem is PlannerVariantItem item)
        {
            if (item.Position != _selectedVariantIndex && !CommitPendingSceneEdits())
            {
                VariantListView.SelectedIndex = _selectedVariantIndex;
                return;
            }

            if (item.Position != _selectedVariantIndex && _isVariantDirty)
            {
                VariantListView.SelectedIndex = _selectedVariantIndex;
                ShowStatus(
                    InfoBarSeverity.Warning,
                    "Save scene edits",
                    "Save the current variant before switching to another variant.");
                return;
            }

            SelectVariant(item);
        }
    }

    private void SelectVariant(PlannerVariantItem item)
    {
        _selectedVariantIndex = item.Position;
        _session.SelectedVariantIndex = item.Position;
        VariantTitleText.Text = item.DisplayName;
        VariantLoglineText.Text = string.IsNullOrWhiteSpace(item.Variant.Logline)
            ? $"{item.Variant.SceneCount} scenes"
            : item.Variant.Logline;
        RefreshSceneList();
    }

    private async void ApplyTimelineButton_Click(object sender, RoutedEventArgs e)
    {
        if (ProjectComboBox.SelectedItem is not ProjectDto project || _selectedVariantIndex < 0)
        {
            ShowStatus(InfoBarSeverity.Warning, "Select a variant", "Choose the variant to apply to Timeline.");
            return;
        }

        if (!CommitPendingSceneEdits())
        {
            return;
        }

        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Apply selected plan to Timeline?",
            Content = "This replaces generated Timeline content with the selected Planner variant.",
            PrimaryButtonText = "Apply plan",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Primary,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        if (!await SaveSelectedVariantAsync())
        {
            return;
        }

        await RunOperationAsync(
            "Applying plan to Timeline",
            cancellationToken => App.Services.ApiClient.ApplyPlanToTimelineAsync(
                project.Id,
                _selectedVariantIndex,
                true,
                StudioPageHelpers.ExpectedRevision(_project),
                cancellationToken),
            successMessage: "The selected variant is now applied to Timeline.",
            afterSuccess: cancellationToken => RefreshProjectRevisionAsync(project.Id, cancellationToken));
    }

    private void SceneListView_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressSceneSelection || SceneListView.SelectedItem is not PlannerSceneItem item)
        {
            return;
        }

        int previousIndex = _selectedSceneIndex;
        if (item.Index != previousIndex && !CommitPendingSceneEdits())
        {
            SetSceneListSelection(previousIndex);
            return;
        }

        SelectScene(item.Index);
    }

    private void SceneTimingNumberBox_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) =>
        MarkSceneEditorDirty();

    private void ScenePromptTextBox_TextChanged(object sender, TextChangedEventArgs e) =>
        MarkSceneEditorDirty();

    private void PreviousSceneButton_Click(object sender, RoutedEventArgs e) =>
        NavigateScene(-1);

    private void NextSceneButton_Click(object sender, RoutedEventArgs e) =>
        NavigateScene(1);

    private void MoveSceneEarlierButton_Click(object sender, RoutedEventArgs e) =>
        MoveSelectedScene(-1);

    private void MoveSceneLaterButton_Click(object sender, RoutedEventArgs e) =>
        MoveSelectedScene(1);

    private void ApproveSceneButton_Click(object sender, RoutedEventArgs e)
    {
        if (!CommitPendingSceneEdits() || SelectedScene is not { } scene)
        {
            return;
        }

        ReplaceSelectedScene(
            WorkspaceModelHelpers.SetSceneApproval(
                scene,
                !WorkspaceModelHelpers.IsSceneApproved(scene)));
    }

    private void LockSceneButton_Click(object sender, RoutedEventArgs e)
    {
        if (SelectedScene is not { } scene)
        {
            return;
        }

        bool wasLocked = WorkspaceModelHelpers.IsSceneLocked(scene);
        if (!wasLocked && !CommitPendingSceneEdits())
        {
            return;
        }

        scene = SelectedScene ?? scene;
        ReplaceSelectedScene(WorkspaceModelHelpers.SetSceneLocked(scene, !wasLocked));
    }

    private void RepairSceneButton_Click(object sender, RoutedEventArgs e)
    {
        if (!CommitPendingSceneEdits() || SelectedScene is not { } scene)
        {
            return;
        }

        if (WorkspaceModelHelpers.IsSceneLocked(scene))
        {
            ShowStatus(
                InfoBarSeverity.Warning,
                "Scene locked",
                "Unlock the scene before marking it for repair.");
            return;
        }

        ReplaceSelectedScene(WorkspaceModelHelpers.MarkSceneNeedsRepair(scene));
    }

    private async void SaveScenesButton_Click(object sender, RoutedEventArgs e) =>
        _ = await SaveSelectedVariantAsync();

    private async void ImportPlanButton_Click(object sender, RoutedEventArgs e)
    {
        if (!CanReplacePlan("importing a plan"))
        {
            return;
        }

        if (ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            ShowStatus(InfoBarSeverity.Warning, "Select a project", "Choose the destination project first.");
            return;
        }

        var picker = new FileOpenPicker();
        InitializePicker(picker);
        picker.FileTypeFilter.Add(".json");
        var file = await picker.PickSingleFileAsync();
        if (file is null)
        {
            return;
        }

        try
        {
            var json = await FileIO.ReadTextAsync(file);
            var request = JsonSerializer.Deserialize(json, StudioJson.GetTypeInfo<PlannerLabImportRequest>())
                          ?? throw new JsonException("The file did not contain a Planner import document.");
            request.ExpectedRevision = StudioPageHelpers.ExpectedRevision(_project);
            await RunOperationAsync(
                "Importing Planner document",
                async cancellationToken =>
                {
                    var response = await App.Services.ApiClient.ImportPlannerLabAsync(project.Id, request, cancellationToken);
                    _plan = response.Plan;
                    PresentPlan();
                    await RefreshProjectRevisionAsync(project.Id, cancellationToken);
                },
                successMessage: $"Imported {file.Name}.");
        }
        catch (JsonException ex)
        {
            ShowStatus(InfoBarSeverity.Error, "Import failed", ex.Message);
        }
    }

    private async void ExportPlanButton_Click(object sender, RoutedEventArgs e)
    {
        if (_plan is null || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            ShowStatus(InfoBarSeverity.Warning, "Nothing to export", "Generate or import a plan first.");
            return;
        }

        var picker = new FileSavePicker();
        InitializePicker(picker);
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        picker.FileTypeChoices.Add("JSON document", [".json"]);
        picker.SuggestedFileName = $"{SafeFileName(project.Name)}-plan";
        var file = await picker.PickSaveFileAsync();
        if (file is null)
        {
            return;
        }

        await FileIO.WriteTextAsync(file, JsonSerializer.Serialize(_plan, StudioJson.GetTypeInfo<PlanDto>()));
        ShowStatus(InfoBarSeverity.Success, "Plan exported", file.Path);
    }

    private void RefreshSceneList()
    {
        var variant = SelectedVariant;
        if (variant is null || variant.Scenes.Count == 0)
        {
            _selectedSceneIndex = -1;
            SceneListView.ItemsSource = null;
            ClearSceneEditor();
            return;
        }

        int preferredIndex = _selectedSceneIndex >= 0
            ? Math.Min(_selectedSceneIndex, variant.Scenes.Count - 1)
            : 0;
        var items = variant.Scenes
            .Select((scene, index) => new PlannerSceneItem(scene, index, variant.Scenes.Count))
            .ToList();

        _suppressSceneSelection = true;
        try
        {
            SceneListView.ItemsSource = items;
            SceneListView.SelectedIndex = preferredIndex;
        }
        finally
        {
            _suppressSceneSelection = false;
        }

        SelectScene(preferredIndex);
    }

    private void SelectScene(int index)
    {
        var variant = SelectedVariant;
        if (variant is null || index < 0 || index >= variant.Scenes.Count)
        {
            ClearSceneEditor();
            return;
        }

        _selectedSceneIndex = index;
        var scene = variant.Scenes[index];
        _suppressSceneEditorChanges = true;
        try
        {
            SceneSelectionHint.Visibility = Visibility.Collapsed;
            SceneEditorPanel.Visibility = Visibility.Visible;
            ScenePositionText.Text = $"Scene {index + 1} of {variant.Scenes.Count}";
            SceneStartNumberBox.Value = scene.StartSeconds;
            SceneEndNumberBox.Value = scene.EndSeconds;
            ScenePromptTextBox.Text = scene.Prompt;
            SceneNegativePromptTextBox.Text = scene.NegativePrompt ?? string.Empty;
            SceneSettingTextBox.Text = scene.Setting ?? string.Empty;
            SceneShotTypeTextBox.Text = scene.ShotType ?? string.Empty;
            SceneCharacterLockTextBox.Text = scene.CharacterLock ?? string.Empty;
            SceneStyleLockTextBox.Text = scene.StyleLock ?? string.Empty;
            SceneStartStateTextBox.Text = scene.StartState ?? string.Empty;
            SceneEndStateTextBox.Text = scene.EndState ?? string.Empty;
            SceneSubjectTextBox.Text = scene.Subject ?? string.Empty;
            SceneActionTextBox.Text = scene.Action ?? string.Empty;
            SceneCameraTextBox.Text = scene.Camera ?? string.Empty;
            SceneMotionTextBox.Text = scene.Motion ?? string.Empty;
            SceneEnvironmentMotionTextBox.Text = scene.EnvironmentMotion ?? string.Empty;
            SceneContinuityTextBox.Text = scene.ContinuityInstruction ?? string.Empty;
            SceneTransitionTextBox.Text = scene.Transition ?? string.Empty;
        }
        finally
        {
            _suppressSceneEditorChanges = false;
        }

        UpdateCurationControls();
    }

    private void ClearSceneEditor()
    {
        _selectedSceneIndex = -1;
        _suppressSceneEditorChanges = true;
        try
        {
            SceneSelectionHint.Text = "Select a scene to edit timing, prompts, approval, and continuity state.";
            SceneSelectionHint.Visibility = Visibility.Visible;
            SceneEditorPanel.Visibility = Visibility.Collapsed;
            ScenePositionText.Text = string.Empty;
            SceneStartNumberBox.Value = double.NaN;
            SceneEndNumberBox.Value = double.NaN;
            ScenePromptTextBox.Text = string.Empty;
            SceneNegativePromptTextBox.Text = string.Empty;
            SceneSettingTextBox.Text = string.Empty;
            SceneShotTypeTextBox.Text = string.Empty;
            SceneCharacterLockTextBox.Text = string.Empty;
            SceneStyleLockTextBox.Text = string.Empty;
            SceneStartStateTextBox.Text = string.Empty;
            SceneEndStateTextBox.Text = string.Empty;
            SceneSubjectTextBox.Text = string.Empty;
            SceneActionTextBox.Text = string.Empty;
            SceneCameraTextBox.Text = string.Empty;
            SceneMotionTextBox.Text = string.Empty;
            SceneEnvironmentMotionTextBox.Text = string.Empty;
            SceneContinuityTextBox.Text = string.Empty;
            SceneTransitionTextBox.Text = string.Empty;
        }
        finally
        {
            _suppressSceneEditorChanges = false;
        }

        UpdateCurationControls();
    }

    private void UpdateCurationControls()
    {
        var variant = SelectedVariant;
        var scene = SelectedScene;
        bool hasScene = scene is not null;
        bool isLocked = scene is not null && WorkspaceModelHelpers.IsSceneLocked(scene);
        bool canEdit = hasScene && !isLocked && !_isOperationBusy;

        SceneStartNumberBox.IsEnabled = canEdit;
        SceneEndNumberBox.IsEnabled = canEdit;
        ScenePromptTextBox.IsEnabled = canEdit;
        SceneNegativePromptTextBox.IsEnabled = canEdit;
        SceneSettingTextBox.IsEnabled = canEdit;
        SceneShotTypeTextBox.IsEnabled = canEdit;
        SceneCharacterLockTextBox.IsEnabled = canEdit;
        SceneStyleLockTextBox.IsEnabled = canEdit;
        SceneStartStateTextBox.IsEnabled = canEdit;
        SceneEndStateTextBox.IsEnabled = canEdit;
        SceneSubjectTextBox.IsEnabled = canEdit;
        SceneActionTextBox.IsEnabled = canEdit;
        SceneCameraTextBox.IsEnabled = canEdit;
        SceneMotionTextBox.IsEnabled = canEdit;
        SceneEnvironmentMotionTextBox.IsEnabled = canEdit;
        SceneContinuityTextBox.IsEnabled = canEdit;
        SceneTransitionTextBox.IsEnabled = canEdit;
        PreviousSceneButton.IsEnabled = hasScene && _selectedSceneIndex > 0 && !_isOperationBusy;
        NextSceneButton.IsEnabled =
            hasScene &&
            variant is not null &&
            _selectedSceneIndex < variant.Scenes.Count - 1 &&
            !_isOperationBusy;
        MoveSceneEarlierButton.IsEnabled = canEdit && _selectedSceneIndex > 0;
        MoveSceneLaterButton.IsEnabled =
            canEdit &&
            variant is not null &&
            _selectedSceneIndex < variant.Scenes.Count - 1;
        ApproveSceneButton.IsEnabled = hasScene && !_isOperationBusy;
        LockSceneButton.IsEnabled = hasScene && !_isOperationBusy;
        RepairSceneButton.IsEnabled = canEdit;
        SaveScenesButton.IsEnabled = variant is not null && _isVariantDirty && !_isOperationBusy;

        if (scene is null)
        {
            SceneStateText.Text = "No scene selected.";
            CurationStatusText.Text = string.Empty;
            ApproveSceneButton.Content = "Approve";
            LockSceneButton.Content = "Lock";
            return;
        }

        bool isApproved = WorkspaceModelHelpers.IsSceneApproved(scene);
        var stateParts = new List<string>
        {
            string.IsNullOrWhiteSpace(WorkspaceModelHelpers.GetSceneStatus(scene))
                ? "draft"
                : WorkspaceModelHelpers.GetSceneStatus(scene),
        };
        if (isApproved)
        {
            stateParts.Add("approved");
        }

        if (isLocked)
        {
            stateParts.Add("locked");
        }

        SceneStateText.Text = $"State: {string.Join(" · ", stateParts.Distinct(StringComparer.OrdinalIgnoreCase))}";
        CurationStatusText.Text = isLocked
            ? "Unlock this scene to change timing, prompts, order, or repair state."
            : _isVariantDirty
                ? "This variant has unsaved scene changes."
                : "Scene changes are synchronized with the saved variant.";
        ApproveSceneButton.Content = isApproved ? "Clear approval" : "Approve";
        LockSceneButton.Content = isLocked ? "Unlock" : "Lock";
    }

    private void MarkSceneEditorDirty()
    {
        if (_suppressSceneEditorChanges || SelectedScene is null)
        {
            return;
        }

        _isVariantDirty = true;
        UpdateCurationControls();
    }

    private bool CommitPendingSceneEdits()
    {
        if (!_isVariantDirty || SelectedScene is not { } scene)
        {
            return true;
        }

        if (WorkspaceModelHelpers.IsSceneLocked(scene))
        {
            return true;
        }

        double start = SceneStartNumberBox.Value;
        double end = SceneEndNumberBox.Value;
        string prompt = ScenePromptTextBox.Text.Trim();
        if (!double.IsFinite(start) || start < 0 ||
            !double.IsFinite(end) || end <= start)
        {
            ShowStatus(
                InfoBarSeverity.Warning,
                "Review scene timing",
                "Scene timing must be finite and nonnegative, and the end must be later than the start.");
            return false;
        }

        if (string.IsNullOrWhiteSpace(prompt))
        {
            ShowStatus(
                InfoBarSeverity.Warning,
                "Review scene prompt",
                "The scene prompt cannot be empty.");
            return false;
        }

        ReplaceSelectedScene(
            WorkspaceModelHelpers.CloneScene(
                scene,
                startSeconds: start,
                endSeconds: end,
                prompt: prompt,
                negativePrompt: NullIfWhiteSpace(SceneNegativePromptTextBox.Text),
                replaceNegativePrompt: true,
                setting: NullIfWhiteSpace(SceneSettingTextBox.Text),
                shotType: NullIfWhiteSpace(SceneShotTypeTextBox.Text),
                characterLock: NullIfWhiteSpace(SceneCharacterLockTextBox.Text),
                styleLock: NullIfWhiteSpace(SceneStyleLockTextBox.Text),
                startState: NullIfWhiteSpace(SceneStartStateTextBox.Text),
                endState: NullIfWhiteSpace(SceneEndStateTextBox.Text),
                subject: NullIfWhiteSpace(SceneSubjectTextBox.Text),
                action: NullIfWhiteSpace(SceneActionTextBox.Text),
                camera: NullIfWhiteSpace(SceneCameraTextBox.Text),
                motion: NullIfWhiteSpace(SceneMotionTextBox.Text),
                environmentMotion: NullIfWhiteSpace(SceneEnvironmentMotionTextBox.Text),
                continuity: NullIfWhiteSpace(SceneContinuityTextBox.Text),
                transition: NullIfWhiteSpace(SceneTransitionTextBox.Text),
                replaceStoryboardFields: true),
            refreshList: false);
        RefreshSceneList();
        return true;
    }

    private void ReplaceSelectedScene(PlanSceneDto replacement, bool refreshList = true)
    {
        var variant = SelectedVariant;
        if (variant is null || _selectedSceneIndex < 0 || _selectedSceneIndex >= variant.Scenes.Count)
        {
            return;
        }

        variant.Scenes[_selectedSceneIndex] = replacement;
        var normalizedScenes = WorkspaceModelHelpers.NormalizeStoryboardContinuity(
            variant.Scenes,
            replacement.CharacterLock,
            replacement.StyleLock);
        variant.Scenes.Clear();
        variant.Scenes.AddRange(normalizedScenes);
        _isVariantDirty = true;
        SynchronizeRawPlan();
        if (refreshList)
        {
            RefreshSceneList();
        }
        else
        {
            UpdateCurationControls();
        }
    }

    private void NavigateScene(int offset)
    {
        var variant = SelectedVariant;
        int targetIndex = _selectedSceneIndex + offset;
        if (variant is null || targetIndex < 0 || targetIndex >= variant.Scenes.Count)
        {
            return;
        }

        if (!CommitPendingSceneEdits())
        {
            return;
        }

        SetSceneListSelection(targetIndex);
        SelectScene(targetIndex);
    }

    private void MoveSelectedScene(int offset)
    {
        var variant = SelectedVariant;
        int targetIndex = _selectedSceneIndex + offset;
        if (variant is null || SelectedScene is not { } scene ||
            targetIndex < 0 || targetIndex >= variant.Scenes.Count)
        {
            return;
        }

        if (WorkspaceModelHelpers.IsSceneLocked(scene))
        {
            ShowStatus(InfoBarSeverity.Warning, "Scene locked", "Unlock the scene before moving it.");
            return;
        }

        if (!CommitPendingSceneEdits())
        {
            return;
        }

        var normalizedCurrentScenes = WorkspaceModelHelpers.NormalizeStoryboardContinuity(
            variant.Scenes);
        var reordered = WorkspaceModelHelpers.NormalizeStoryboardContinuity(
            WorkspaceModelHelpers.MoveScene(normalizedCurrentScenes, _selectedSceneIndex, offset));
        variant.Scenes.Clear();
        variant.Scenes.AddRange(reordered);
        _selectedSceneIndex = Math.Clamp(targetIndex, 0, variant.Scenes.Count - 1);
        _isVariantDirty = true;
        SynchronizeRawPlan();
        RefreshSceneList();
    }

    private void SetSceneListSelection(int index)
    {
        _suppressSceneSelection = true;
        try
        {
            SceneListView.SelectedIndex = index;
        }
        finally
        {
            _suppressSceneSelection = false;
        }
    }

    private async Task<bool> SaveSelectedVariantAsync()
    {
        if (!CommitPendingSceneEdits())
        {
            return false;
        }

        if (!_isVariantDirty)
        {
            return true;
        }

        if (ProjectComboBox.SelectedItem is not ProjectDto project ||
            SelectedVariant is not { } variant)
        {
            ShowStatus(InfoBarSeverity.Warning, "Cannot save scenes", "Select a project and plan variant first.");
            return false;
        }

        int sceneIndex = _selectedSceneIndex;
        bool saved = false;
        await RunOperationAsync(
            "Saving curated scenes",
            async cancellationToken =>
            {
                var response = await App.Services.ApiClient.UpdatePlanVariantAsync(
                    project.Id,
                    _selectedVariantIndex,
                    variant.Scenes,
                    StudioPageHelpers.ExpectedRevision(_project),
                    cancellationToken);
                if (!response.Ok || response.Plan is null)
                {
                    throw new InvalidDataException("The backend did not return the normalized saved plan.");
                }

                _plan = response.Plan;
                int normalizedVariantIndex = response.VariantIndex ?? _selectedVariantIndex;
                if (normalizedVariantIndex < 0 || normalizedVariantIndex >= _plan.Variants.Count)
                {
                    throw new InvalidDataException("The backend returned an invalid saved variant index.");
                }

                _selectedVariantIndex = normalizedVariantIndex;
                _selectedSceneIndex = sceneIndex;
                _session.SelectedVariantIndex = _selectedVariantIndex;
                PresentPlan();
                await RefreshProjectRevisionAsync(project.Id, cancellationToken);
                saved = true;
            },
            successMessage: "Curated scenes were saved to the project.");
        return saved;
    }

    private bool CanReplacePlan(string action)
    {
        if (!_isVariantDirty || CommitPendingSceneEdits() && !_isVariantDirty)
        {
            return true;
        }

        ShowStatus(
            InfoBarSeverity.Warning,
            "Save scene edits",
            $"Save the current variant before {action}.");
        return false;
    }

    private void RestoreActiveProjectSelection()
    {
        var activeProject = _projects.FirstOrDefault(
            project => string.Equals(project.Id, _session.ActiveProjectId, StringComparison.Ordinal));
        _isLoadingProject = true;
        ProjectComboBox.SelectedItem = activeProject;
        _isLoadingProject = false;
    }

    private void CancelPlannerButton_Click(object sender, RoutedEventArgs e) =>
        _operationCancellation?.Cancel();

    private void OpenWorkspaceButton_Click(object sender, RoutedEventArgs e) => App.Navigate("workspace");

    private void OpenTimelineButton_Click(object sender, RoutedEventArgs e) => App.Navigate("timeline");

    private void OpenRenderButton_Click(object sender, RoutedEventArgs e) => App.Navigate("render");

    private async Task RunOperationAsync(
        string title,
        Func<CancellationToken, Task> operation,
        string? successMessage = null,
        Func<CancellationToken, Task>? afterSuccess = null)
    {
        _operationCancellation?.Cancel();
        var operationCancellation = new CancellationTokenSource();
        _operationCancellation = operationCancellation;
        SetBusy(true);
        ShowStatus(InfoBarSeverity.Informational, title, "Working…");
        try
        {
            await operation(operationCancellation.Token);
            if (afterSuccess is not null)
            {
                await afterSuccess(operationCancellation.Token);
            }

            if (!string.IsNullOrWhiteSpace(successMessage))
            {
                ShowStatus(InfoBarSeverity.Success, "Planner updated", successMessage);
            }
        }
        catch (OperationCanceledException) when (operationCancellation.IsCancellationRequested)
        {
            ShowStatus(InfoBarSeverity.Warning, "Operation canceled", "No additional Planner changes were requested.");
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict, operationCancellation.Token);
        }
        catch (StudioApiException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.UserFacingMessage);
        }
        catch (InvalidDataException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.Message);
        }
        catch (HttpRequestException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.Message);
        }
        finally
        {
            operationCancellation.Dispose();
            if (ReferenceEquals(_operationCancellation, operationCancellation))
            {
                _operationCancellation = null;
                SetBusy(false);
            }
        }
    }

    private async Task RunOperationAsync<T>(
        string title,
        Func<CancellationToken, Task<T>> operation,
        string? successMessage = null) =>
        await RunOperationAsync(title, async cancellationToken => _ = await operation(cancellationToken), successMessage);

    private async Task RefreshProjectRevisionAsync(string projectId, CancellationToken cancellationToken)
    {
        ProjectResponse refreshed = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        _project = refreshed.Project;
        _session.ActiveProjectId = refreshed.Project.Id;
    }

    private async Task HandleProjectRevisionConflictAsync(
        ProjectRevisionConflictException conflict,
        CancellationToken cancellationToken)
    {
        if (!await StudioPageHelpers.ConfirmReloadAfterRevisionConflictAsync(XamlRoot, conflict))
        {
            ShowStatus(
                InfoBarSeverity.Warning,
                "Project reload required",
                "The failed change was not applied. Review your local Planner work, reload, then retry.");
            return;
        }

        string? projectId = _project?.Id ?? _session.ActiveProjectId;
        if (string.IsNullOrWhiteSpace(projectId))
        {
            return;
        }

        try
        {
            await LoadProjectAsync(projectId, cancellationToken);
            ShowStatus(
                InfoBarSeverity.Informational,
                "Project reloaded",
                "The latest revision is loaded. Review the plan, then retry your change.");
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (StudioApiException reloadError)
        {
            ShowStatus(InfoBarSeverity.Error, "Reload failed", reloadError.UserFacingMessage);
        }
        catch (HttpRequestException reloadError)
        {
            ShowStatus(InfoBarSeverity.Error, "Reload failed", reloadError.Message);
        }
        catch (JsonException reloadError)
        {
            ShowStatus(InfoBarSeverity.Error, "Reload failed", reloadError.Message);
        }
    }

    private void SetBusy(bool isBusy)
    {
        _isOperationBusy = isBusy;
        PlannerProgressRing.IsActive = isBusy;
        PlannerProgressRing.Visibility = isBusy ? Visibility.Visible : Visibility.Collapsed;
        CancelPlannerButton.IsEnabled = isBusy;
        RefreshPlannerButton.IsEnabled = !isBusy;
        GeneratePlanButton.IsEnabled = !isBusy;
        ProjectComboBox.IsEnabled = !isBusy;
        UpdateCurationControls();
    }

    private void ShowStatus(InfoBarSeverity severity, string title, string message)
    {
        PlannerInfoBar.Severity = severity;
        PlannerInfoBar.Title = title;
        PlannerInfoBar.Message = message;
        PlannerInfoBar.IsOpen = true;
    }

    private void ClearPlan()
    {
        _plan = null;
        _selectedVariantIndex = -1;
        _selectedSceneIndex = -1;
        _isVariantDirty = false;
        VariantListView.ItemsSource = null;
        SceneListView.ItemsSource = null;
        RawPlanTextBox.Text = string.Empty;
        VariantTitleText.Text = "Select a variant";
        VariantLoglineText.Text = "Generated scene structure and raw backend data will appear here.";
        ClearSceneEditor();
    }

    private void SynchronizeRawPlan()
    {
        RawPlanTextBox.Text = _plan is null
            ? string.Empty
            : JsonSerializer.Serialize(_plan, StudioJson.GetTypeInfo<PlanDto>());
    }

    private static void InitializePicker(object picker)
    {
        var window = App.MainWindowInstance
                     ?? throw new InvalidOperationException("The main window is not available.");
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
    }

    private static string FormatJson(JsonElement value) =>
        JsonSerializer.Serialize(value, _indentedJsonContext.JsonElement);

    private static string? NullIfWhiteSpace(string? value) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim();

    private static string SafeFileName(string value) =>
        string.Concat(value.Select(character => Path.GetInvalidFileNameChars().Contains(character) ? '-' : character));

    private sealed record PlannerVariantItem(PlanVariantDto Variant, int Position)
    {
        public string DisplayName => Variant.DisplayName;

        public string Summary =>
            $"{Variant.SceneCount} scenes" +
            (Variant.DurationSeconds is double duration ? $" · {duration:0.#} s" : string.Empty);
    }

    private sealed record PlannerSceneItem(PlanSceneDto Scene, int Index, int Total)
    {
        public string Position => $"Scene {Index + 1} of {Total}";

        public string TimeRange => $"{Scene.StartSeconds:0.##}–{Scene.EndSeconds:0.##} s";

        public string Prompt => Scene.Prompt;

        public string NegativePrompt => string.IsNullOrWhiteSpace(Scene.NegativePrompt)
            ? string.Empty
            : $"Avoid: {Scene.NegativePrompt}";

        public string MotionSummary
        {
            get
            {
                var parts = new[]
                {
                    Scene.Action,
                    Scene.Motion,
                    Scene.EnvironmentMotion,
                    Scene.ContinuityInstruction,
                };
                return string.Join(
                    " · ",
                    parts.Where(value => !string.IsNullOrWhiteSpace(value)).Take(2));
            }
        }

        public string StateSummary
        {
            get
            {
                var parts = new List<string>
                {
                    string.IsNullOrWhiteSpace(WorkspaceModelHelpers.GetSceneStatus(Scene))
                        ? "draft"
                        : WorkspaceModelHelpers.GetSceneStatus(Scene),
                };
                if (WorkspaceModelHelpers.IsSceneApproved(Scene))
                {
                    parts.Add("approved");
                }

                if (WorkspaceModelHelpers.IsSceneLocked(Scene))
                {
                    parts.Add("locked");
                }

                return string.Join(" · ", parts.Distinct(StringComparer.OrdinalIgnoreCase));
            }
        }
    }
}
