using System.Collections.ObjectModel;
using System.Text.Json;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.Storage.Pickers;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class ReactiveLabPage : Page
{
    private static readonly StudioJsonContext _indentedJsonContext =
        new(new JsonSerializerOptions { WriteIndented = true });
    private readonly ObservableCollection<ReactiveMapping> _mappings = [];
    private readonly string[] _mappingPresets = ["cinematic", "psychedelic", "ambient", "percussive"];
    private readonly ObservableCollection<string> _presetNames = [];
    private readonly List<ReactivePreset> _savedPresets = [];
    private IReadOnlyList<ProjectDto> _projects = [];
    private ProjectDto? _project;
    private MusicGraphResponse? _musicGraph;
    private LiveCuesResponse? _liveCues;
    private LiveAssetsResponse? _liveAssets;
    private JsonElement? _timeline;
    private ReactiveLabApplyRequest _draftRequest = new();
    private ReactivePreset _currentPreset = new();
    private CancellationTokenSource? _operationCancellation;
    private string? _activeProjectId;
    private int _selectedVariantIndex;
    private bool _isUpdatingEditor;
    private bool _isSynchronizingProject;
    private bool _isLoadingPreset;
    private bool _isSessionSubscribed;

    public ObservableCollection<ReactiveMapping> Mappings => _mappings;

    public ReactiveLabPage()
    {
        InitializeComponent();
        InitializeOptions();
        Loaded += ReactiveLabPage_Loaded;
        Unloaded += ReactiveLabPage_Unloaded;
    }

    private void InitializeOptions()
    {
        SourceSignalComboBox.ItemsSource = new[]
        {
            "rms", "bass", "mid", "treble", "spectral_centroid", "spectral_flux",
            "onset_strength", "beat", "section_energy", "cue"
        };
        TargetParameterComboBox.ItemsSource = new[]
        {
            "motion.strength", "camera.zoom", "camera.pan", "camera.tilt",
            "camera.rotation", "animation.cadence", "animation.noise",
            "render.guidance", "render.strength", "visual.intensity"
        };
        ResponseCurveComboBox.ItemsSource = new[] { "linear", "ease-in", "ease-out", "smoothstep", "exponential", "logarithmic" };
        GrammarComboBox.ItemsSource = new[] { "continuous", "pulse", "gate", "accent", "hold", "section", "cue" };
        QuantizationComboBox.ItemsSource = new[] { "none", "1/16", "1/8", "1/4", "1/2", "1 bar", "2 bars", "section", "cue" };
        MappingPresetComboBox.ItemsSource = _mappingPresets;
        MappingPresetComboBox.SelectedItem = "cinematic";
        RenderModeComboBox.ItemsSource = new[] { "performance", "balanced", "quality" };
        RenderModeComboBox.SelectedItem = "balanced";
        SectionComboBox.ItemsSource = Array.Empty<string>();
        CueComboBox.ItemsSource = Array.Empty<string>();
        PresetComboBox.ItemsSource = _presetNames;
        UpdatePresetNames();
    }

    private async void ReactiveLabPage_Loaded(object sender, RoutedEventArgs e)
    {
        if (!_isSessionSubscribed)
        {
            App.Services.Session.Changed += Session_Changed;
            _isSessionSubscribed = true;
        }

        await RunOperationAsync("Loading Reactive Lab", LoadProjectsAndContextAsync);
    }

    private void ReactiveLabPage_Unloaded(object sender, RoutedEventArgs e)
    {
        _operationCancellation?.Cancel();
        if (_isSessionSubscribed)
        {
            App.Services.Session.Changed -= Session_Changed;
            _isSessionSubscribed = false;
        }
    }

    private async Task RefreshProjectRevisionAsync(string projectId, CancellationToken cancellationToken)
    {
        ProjectResponse refreshed = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        if (string.Equals(projectId, _activeProjectId, StringComparison.Ordinal))
        {
            _project = refreshed.Project;
        }
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
                "The failed change was not applied. Review your local Reactive Lab draft, reload, then retry.");
            return;
        }

        try
        {
            await RefreshContextAsync(cancellationToken);
            ShowStatus(
                InfoBarSeverity.Informational,
                "Project reloaded",
                "The latest revision is loaded. Review the Reactive Lab payload, then retry your change.");
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

    private async void Session_Changed(object? sender, EventArgs e)
    {
        if (_isSynchronizingProject)
        {
            return;
        }

        var sessionProjectId = App.Services.Session.ActiveProjectId;
        if (!string.Equals(sessionProjectId, _activeProjectId, StringComparison.Ordinal))
        {
            _activeProjectId = sessionProjectId;
            SelectProject(_activeProjectId);
            await RunOperationAsync("Synchronizing project", RefreshContextAsync);
            return;
        }

        _selectedVariantIndex = App.Services.Session.SelectedVariantIndex;
        UpdatePlanSummary();
    }

    private async Task LoadProjectsAndContextAsync(CancellationToken cancellationToken)
    {
        var response = await App.Services.ApiClient.GetProjectsAsync(cancellationToken);
        _projects = response.Projects;
        ProjectComboBox.ItemsSource = _projects;
        _activeProjectId = App.Services.Session.ActiveProjectId;
        if (string.IsNullOrWhiteSpace(_activeProjectId) && _projects.Count > 0)
        {
            _activeProjectId = _projects[0].Id;
            SynchronizeSessionProject(_activeProjectId);
        }

        SelectProject(_activeProjectId);
        await RefreshContextAsync(cancellationToken);
    }

    private async Task RefreshContextAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_activeProjectId))
        {
            ClearContext();
            ShowStatus(InfoBarSeverity.Warning, "No active project", "Create or select a project before using Reactive Lab.");
            return;
        }

        var projectTask = App.Services.ApiClient.GetProjectAsync(_activeProjectId, cancellationToken);
        var graphTask = App.Services.ApiClient.GetProjectMusicGraphAsync(_activeProjectId, cancellationToken);
        var cueTask = App.Services.ApiClient.GetProjectLiveCuesAsync(_activeProjectId, cancellationToken);
        var assetTask = App.Services.ApiClient.GetProjectLiveAssetsAsync(_activeProjectId, cancellationToken);
        var timelineTask = App.Services.ApiClient.GetTimelineAsync(_activeProjectId, cancellationToken);
        var localStateTask = LoadLocalStateAsync(_activeProjectId);

        await Task.WhenAll(projectTask, graphTask, cueTask, assetTask, timelineTask, localStateTask);

        var project = await projectTask;
        var graph = await graphTask;
        var cues = await cueTask;
        var assets = await assetTask;
        var timeline = await timelineTask;
        var localState = await localStateTask;

        _project = project.Project;
        _musicGraph = graph;
        _liveCues = cues;
        _liveAssets = assets;
        _timeline = timeline;
        LoadBackendDraft(project.Project);
        LoadLocalState(localState);
        _selectedVariantIndex = App.Services.Session.SelectedVariantIndex;
        UpdateContextSelectors();

        MusicGraphSummaryTextBlock.Text =
            $"{graph.Tempo.Bpm:0.#} BPM · {graph.Beats.Count} beats · {graph.Sections.Count} sections · {graph.Stems.Count} stems";
        LiveCuesSummaryTextBlock.Text =
            $"{cues.EventCount} events · {cues.Bpm:0.#} BPM · {(cues.AdvisoryOnly ? "advisory" : "authoritative")}";
        LiveAssetsSummaryTextBlock.Text =
            $"{assets.PackCount} packs · {assets.ChannelCount} channels · {(assets.Ready ? "ready" : "not ready")} · {assets.LatencyBudgetMilliseconds} ms budget";
        TimelineSummaryTextBlock.Text = SummarizeTimeline(timeline);
        UpdatePlanSummary(project.Project);
        UpdateRawJson();
        UpdateDiagnostics();
    }

    private void LoadBackendDraft(ProjectDto project)
    {
        _draftRequest = new ReactiveLabApplyRequest();
        if (project.Meta.ValueKind != JsonValueKind.Object ||
            !project.Meta.TryGetProperty("last_reactive_lab", out var lastReactive) ||
            lastReactive.ValueKind != JsonValueKind.Object)
        {
            return;
        }

        _draftRequest = JsonSerializer.Deserialize(
                lastReactive.GetRawText(),
                StudioJsonContext.Default.ReactiveLabApplyRequest)
            ?? new ReactiveLabApplyRequest();
    }

    private void LoadLocalState(ReactiveLabLocalState? state)
    {
        _savedPresets.Clear();
        if (state is not null)
        {
            _savedPresets.AddRange(state.Presets);
            _currentPreset = state.Current;
        }
        else if (_draftRequest.Metadata is JsonElement metadataElement)
        {
            var metadata = metadataElement.Deserialize(StudioJsonContext.Default.ReactiveLabMetadata);
            _currentPreset = metadata is null
                ? new ReactivePreset()
                : new ReactivePreset
                {
                    Name = metadata.Settings.Name,
                    Mappings = metadata.Mappings.Count > 0 ? metadata.Mappings : metadata.Settings.Mappings,
                    MappingPreset = metadata.Settings.MappingPreset,
                    Sensitivity = metadata.Settings.Sensitivity,
                    Smoothing = metadata.Settings.Smoothing,
                    FramesPerSecond = metadata.Settings.FramesPerSecond,
                    MinimumCutFrames = metadata.Settings.MinimumCutFrames,
                    RenderMode = metadata.Settings.RenderMode,
                    ScheduleStride = metadata.Settings.ScheduleStride,
                    Scaling = metadata.Settings.Scaling,
                    ExtensionData = metadata.Settings.ExtensionData
                };
        }
        else
        {
            _currentPreset = new ReactivePreset();
        }

        var mappings = _currentPreset.Mappings.Count > 0
            ? _currentPreset.Mappings
            : ReadMappingsFromMetadata(_draftRequest.Metadata);
        ReplaceMappings(mappings);
        ApplyPresetSettings(_currentPreset);
        OverwriteMotionTrackToggle.IsOn = _draftRequest.OverwriteMotionTrack;
        OverwriteCameraToggle.IsOn = _draftRequest.OverwriteCamera;
        UpdatePresetNames();
    }

    private void UpdateContextSelectors()
    {
        SectionComboBox.ItemsSource = _musicGraph?.Sections
            .Select(section => section.Label)
            .Where(label => !string.IsNullOrWhiteSpace(label))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray() ?? [];

        var cues = new List<string>();
        foreach (var item in _liveCues?.Events ?? [])
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                var value = item.GetString();
                if (!string.IsNullOrWhiteSpace(value))
                {
                    cues.Add(value);
                }
            }
            else if (item.ValueKind == JsonValueKind.Object)
            {
                foreach (var propertyName in new[] { "id", "cue_id", "name", "label" })
                {
                    if (item.TryGetProperty(propertyName, out var property) &&
                        property.ValueKind == JsonValueKind.String &&
                        !string.IsNullOrWhiteSpace(property.GetString()))
                    {
                        cues.Add(property.GetString()!);
                        break;
                    }
                }
            }
        }

        CueComboBox.ItemsSource = cues.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private async void ProjectComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isSynchronizingProject || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            return;
        }

        _activeProjectId = project.Id;
        SynchronizeSessionProject(project.Id);
        await RunOperationAsync("Loading project context", RefreshContextAsync);
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e) =>
        await RunOperationAsync("Refreshing Reactive Lab", RefreshContextAsync, "Music, cue, asset, plan, and Timeline context refreshed.");

    private void CancelButton_Click(object sender, RoutedEventArgs e) =>
        _operationCancellation?.Cancel();

    private void WorkspaceButton_Click(object sender, RoutedEventArgs e) => App.Navigate("workspace");

    private void TimelineButton_Click(object sender, RoutedEventArgs e) => App.Navigate("timeline");

    private void RenderButton_Click(object sender, RoutedEventArgs e) => App.Navigate("render");

    private async void ImportButton_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker();
        picker.FileTypeFilter.Add(".json");
        InitializePicker(picker);
        var file = await picker.PickSingleFileAsync();
        if (file is null)
        {
            return;
        }

        await RunOperationAsync("Importing Reactive payload", async cancellationToken =>
        {
            cancellationToken.ThrowIfCancellationRequested();
            var text = await FileIO.ReadTextAsync(file);
            cancellationToken.ThrowIfCancellationRequested();
            UseRawJson(text);
            await SaveLocalStateAsync();
        }, $"Imported {file.Name}. Review and apply when ready.");
    }

    private async void ExportButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryBuildRequest(out var request, out var errors))
        {
            ShowValidationErrors(errors);
            return;
        }

        var picker = new FileSavePicker
        {
            SuggestedFileName = $"{SafeFileName(SelectedProjectName())}-reactive-lab"
        };
        picker.FileTypeChoices.Add("JSON", [".json"]);
        InitializePicker(picker);
        var file = await picker.PickSaveFileAsync();
        if (file is null)
        {
            return;
        }

        var json = JsonSerializer.Serialize(request, StudioJsonContext.Default.ReactiveLabApplyRequest);
        await FileIO.WriteTextAsync(file, FormatJson(json));
        ShowStatus(InfoBarSeverity.Success, "Reactive payload exported", file.Name);
    }

    private async void ApplyButton_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_activeProjectId))
        {
            ShowStatus(InfoBarSeverity.Warning, "No active project", "Select a project before applying Reactive Lab results.");
            return;
        }

        if (!TryBuildRequest(out var request, out var errors))
        {
            ShowValidationErrors(errors);
            return;
        }

        if (!ReactiveWorkflow.HasMeaningfulPayload(request))
        {
            ShowStatus(
                InfoBarSeverity.Warning,
                "Analyzed results required",
                "Mappings configure reactions but do not generate Timeline data by themselves. Import or load keyframes, cues, sections, schedules, or a handoff manifest first.");
            return;
        }

        await RunOperationAsync("Applying Reactive Lab results", async cancellationToken =>
        {
            _draftRequest = request;
            var response = await App.Services.ApiClient.ApplyReactiveLabAsync(_activeProjectId, request, cancellationToken);
            TimelineSummaryTextBlock.Text = SummarizeTimeline(response.Timeline);
            RawJsonTextBox.Text = FormatJson(JsonSerializer.Serialize(
                request,
                StudioJsonContext.Default.ReactiveLabApplyRequest));
            await SaveLocalStateAsync();
            await RefreshProjectRevisionAsync(_activeProjectId, cancellationToken);
        }, "Reactive results were persisted and merged into the project Timeline.");
    }

    private void AddMappingButton_Click(object sender, RoutedEventArgs e)
    {
        var mapping = new ReactiveMapping { Name = $"Mapping {_mappings.Count + 1}" };
        _mappings.Add(mapping);
        MappingListView.SelectedItem = mapping;
        PersistMappingChange();
    }

    private void DuplicateMappingButton_Click(object sender, RoutedEventArgs e)
    {
        if (MappingListView.SelectedItem is not ReactiveMapping selected)
        {
            return;
        }

        var duplicate = ReactiveWorkflow.Duplicate(selected, Guid.NewGuid().ToString("N"));
        var index = MappingListView.SelectedIndex + 1;
        _mappings.Insert(index, duplicate);
        MappingListView.SelectedIndex = index;
        PersistMappingChange();
    }

    private void MoveMappingUpButton_Click(object sender, RoutedEventArgs e) => MoveSelectedMapping(-1);

    private void MoveMappingDownButton_Click(object sender, RoutedEventArgs e) => MoveSelectedMapping(1);

    private async void DeleteMappingButton_Click(object sender, RoutedEventArgs e)
    {
        if (MappingListView.SelectedItem is not ReactiveMapping selected)
        {
            return;
        }

        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Delete reactive mapping?",
            Content = $"Delete “{selected.Name}”? This cannot be undone.",
            PrimaryButtonText = "Delete",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        var index = MappingListView.SelectedIndex;
        _mappings.Remove(selected);
        MappingListView.SelectedIndex = Math.Min(index, _mappings.Count - 1);
        PersistMappingChange();
    }

    private void MappingListView_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var mapping = MappingListView.SelectedItem as ReactiveMapping;
        SetMappingEditorEnabled(mapping is not null);
        DuplicateMappingButton.IsEnabled = mapping is not null;
        DeleteMappingButton.IsEnabled = mapping is not null;
        MoveMappingUpButton.IsEnabled = MappingListView.SelectedIndex > 0;
        MoveMappingDownButton.IsEnabled =
            MappingListView.SelectedIndex >= 0 && MappingListView.SelectedIndex < _mappings.Count - 1;
        if (mapping is not null)
        {
            PopulateMappingEditor(mapping);
        }
    }

    private async void MappingEditor_Changed(object sender, object e) =>
        await UpdateMappingFromEditorAsync();

    private async void MappingEditor_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args) =>
        await UpdateMappingFromEditorAsync();

    private async Task UpdateMappingFromEditorAsync()
    {
        if (_isUpdatingEditor || MappingListView.SelectedIndex < 0)
        {
            return;
        }

        var index = MappingListView.SelectedIndex;
        var existing = _mappings[index];
        var updated = existing with
        {
            Name = MappingNameTextBox.Text.Trim(),
            IsEnabled = MappingEnabledToggle.IsOn,
            SourceSignal = ComboText(SourceSignalComboBox),
            TargetParameter = ComboText(TargetParameterComboBox),
            ResponseCurve = ComboText(ResponseCurveComboBox),
            Grammar = ComboText(GrammarComboBox),
            Gain = FiniteValue(GainNumberBox.Value, existing.Gain),
            Smoothing = FiniteValue(MappingSmoothingNumberBox.Value, existing.Smoothing),
            Threshold = FiniteValue(ThresholdNumberBox.Value, existing.Threshold),
            InputMinimum = FiniteValue(InputMinimumNumberBox.Value, existing.InputMinimum),
            InputMaximum = FiniteValue(InputMaximumNumberBox.Value, existing.InputMaximum),
            OutputMinimum = FiniteValue(OutputMinimumNumberBox.Value, existing.OutputMinimum),
            OutputMaximum = FiniteValue(OutputMaximumNumberBox.Value, existing.OutputMaximum),
            Quantization = ComboText(QuantizationComboBox),
            Section = NullIfWhiteSpace(SectionComboBox.SelectedItem?.ToString()),
            Cue = NullIfWhiteSpace(CueComboBox.SelectedItem?.ToString())
        };
        _mappings[index] = updated;
        MappingListView.SelectedIndex = index;
        UpdateDiagnostics();
        UpdateRawJson();
        await SaveLocalStateAsync();
    }

    private void SetMappingEditorEnabled(bool isEnabled)
    {
        foreach (var control in new Control[]
                 {
                     MappingEnabledToggle,
                     MappingNameTextBox,
                     SourceSignalComboBox,
                     TargetParameterComboBox,
                     ResponseCurveComboBox,
                     GrammarComboBox,
                     QuantizationComboBox,
                     GainNumberBox,
                     MappingSmoothingNumberBox,
                     ThresholdNumberBox,
                     InputMinimumNumberBox,
                     InputMaximumNumberBox,
                     OutputMinimumNumberBox,
                     OutputMaximumNumberBox,
                     SectionComboBox,
                     CueComboBox
                 })
        {
            control.IsEnabled = isEnabled;
        }
    }

    private async void PresetComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isLoadingPreset || PresetComboBox.SelectedItem is not string name)
        {
            return;
        }

        var preset = _savedPresets.FirstOrDefault(item => string.Equals(item.Name, name, StringComparison.Ordinal));
        if (preset is null)
        {
            return;
        }

        _currentPreset = preset;
        ReplaceMappings(preset.Mappings);
        ApplyPresetSettings(preset);
        UpdateRawJson();
        UpdateDiagnostics();
        await SaveLocalStateAsync();
        ShowStatus(InfoBarSeverity.Success, "Preset applied", $"Applied “{preset.Name}” to the local editor.");
    }

    private async void SavePresetButton_Click(object sender, RoutedEventArgs e)
    {
        var name = PresetNameTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            ShowStatus(InfoBarSeverity.Warning, "Preset name required", "Enter a name before saving this project preset.");
            return;
        }

        var preset = BuildCurrentPreset(name);
        var index = _savedPresets.FindIndex(item => string.Equals(item.Name, name, StringComparison.Ordinal));
        if (index >= 0)
        {
            _savedPresets[index] = preset;
        }
        else
        {
            _savedPresets.Add(preset);
        }

        _currentPreset = preset;
        UpdatePresetNames(name);
        await SaveLocalStateAsync();
        ShowStatus(InfoBarSeverity.Success, "Preset saved", $"Saved “{name}” for this project.");
    }

    private async void DeletePresetButton_Click(object sender, RoutedEventArgs e)
    {
        if (PresetComboBox.SelectedItem is not string name)
        {
            return;
        }

        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Delete project preset?",
            Content = $"Delete “{name}” from this project’s local Reactive Lab presets?",
            PrimaryButtonText = "Delete",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        _savedPresets.RemoveAll(item => string.Equals(item.Name, name, StringComparison.Ordinal));
        UpdatePresetNames();
        await SaveLocalStateAsync();
        ShowStatus(InfoBarSeverity.Success, "Preset deleted", $"Deleted “{name}”.");
    }

    private void ValidateJsonButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var request = ParseRequest(RawJsonTextBox.Text);
            var errors = ValidateRequest(request);
            if (errors.Count == 0)
            {
                ShowStatus(InfoBarSeverity.Success, "JSON is valid", "The payload is structurally valid and contains authoritative Timeline content.");
            }
            else
            {
                ShowValidationErrors(errors);
            }
        }
        catch (JsonException ex)
        {
            ShowStatus(InfoBarSeverity.Error, "Invalid JSON", ex.Message);
        }
    }

    private async void UseJsonButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            UseRawJson(RawJsonTextBox.Text);
            await SaveLocalStateAsync();
            ShowStatus(InfoBarSeverity.Success, "JSON loaded", "The raw payload is now the Reactive Lab draft.");
        }
        catch (JsonException ex)
        {
            ShowStatus(InfoBarSeverity.Error, "Invalid JSON", ex.Message);
        }
    }

    private void UseRawJson(string json)
    {
        _draftRequest = ParseRequest(json);
        var metadataMappings = ReadMappingsFromMetadata(_draftRequest.Metadata);
        if (metadataMappings.Count > 0)
        {
            ReplaceMappings(metadataMappings);
        }

        RawJsonTextBox.Text = FormatJson(JsonSerializer.Serialize(
            _draftRequest,
            StudioJsonContext.Default.ReactiveLabApplyRequest));
        UpdateDiagnostics();
    }

    private bool TryBuildRequest(out ReactiveLabApplyRequest request, out IReadOnlyList<string> errors)
    {
        _currentPreset = BuildCurrentPreset("Current");
        var metadata = BuildMetadata(_draftRequest.Metadata, _currentPreset);
        request = new ReactiveLabApplyRequest
        {
            Metadata = metadata,
            Keyframes = _draftRequest.Keyframes,
            BeatMarkers = _draftRequest.BeatMarkers,
            CueEvents = _draftRequest.CueEvents,
            Sections = _draftRequest.Sections,
            RepairSuggestions = _draftRequest.RepairSuggestions,
            Schedules = _draftRequest.Schedules,
            HandoffManifest = _draftRequest.HandoffManifest,
            ExtensionData = _draftRequest.ExtensionData,
            OverwriteMotionTrack = OverwriteMotionTrackToggle.IsOn,
            OverwriteCamera = OverwriteCameraToggle.IsOn,
            ExpectedRevision = StudioPageHelpers.ExpectedRevision(_project),
        };
        errors = ValidateRequest(request);
        return errors.Count == 0;
    }

    private IReadOnlyList<string> ValidateRequest(ReactiveLabApplyRequest request)
    {
        var errors = _mappings
            .SelectMany((mapping, index) => ReactiveWorkflow.ValidateMapping(mapping)
                .Select(error => $"Mapping {index + 1}: {error}"))
            .ToList();
        if (!ReactiveWorkflow.HasMeaningfulPayload(request))
        {
            errors.Add("The payload must contain analyzed keyframes, beats, cues, sections, repairs, schedules, or a handoff manifest.");
        }

        return errors;
    }

    private JsonElement BuildMetadata(JsonElement? existing, ReactivePreset settings)
    {
        var extensionData = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        if (existing?.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in existing.Value.EnumerateObject())
            {
                if (property.Name is not ("source" or "selected_variant_index" or "mappings" or "native_mappings" or "settings"))
                {
                    extensionData[property.Name] = property.Value.Clone();
                }
            }
        }

        var metadata = new ReactiveLabMetadata
        {
            SelectedVariantIndex = _selectedVariantIndex,
            Mappings = _mappings.ToList(),
            Settings = settings,
            ExtensionData = extensionData.Count == 0 ? null : extensionData
        };
        return JsonSerializer.SerializeToElement(metadata, StudioJsonContext.Default.ReactiveLabMetadata);
    }

    private ReactivePreset BuildCurrentPreset(string name) => new()
    {
        Name = name,
        Mappings = _mappings.ToList(),
        MappingPreset = ComboText(MappingPresetComboBox),
        Sensitivity = FiniteValue(SensitivityNumberBox.Value, 1),
        Smoothing = FiniteValue(GlobalSmoothingNumberBox.Value, 0.82),
        FramesPerSecond = (int)FiniteValue(FramesPerSecondNumberBox.Value, 30),
        MinimumCutFrames = (int)FiniteValue(MinimumCutFramesNumberBox.Value, 12),
        RenderMode = ComboText(RenderModeComboBox),
        ScheduleStride = (int)FiniteValue(ScheduleStrideNumberBox.Value, 4),
        Scaling = _currentPreset.Scaling,
        ExtensionData = _currentPreset.ExtensionData
    };

    private void ApplyPresetSettings(ReactivePreset preset)
    {
        SetComboText(MappingPresetComboBox, preset.MappingPreset);
        SensitivityNumberBox.Value = preset.Sensitivity;
        GlobalSmoothingNumberBox.Value = preset.Smoothing;
        FramesPerSecondNumberBox.Value = preset.FramesPerSecond;
        MinimumCutFramesNumberBox.Value = preset.MinimumCutFrames;
        ScheduleStrideNumberBox.Value = preset.ScheduleStride;
        SetComboText(RenderModeComboBox, preset.RenderMode);
    }

    private void PopulateMappingEditor(ReactiveMapping mapping)
    {
        _isUpdatingEditor = true;
        try
        {
            MappingNameTextBox.Text = mapping.Name;
            MappingEnabledToggle.IsOn = mapping.IsEnabled;
            SetComboText(SourceSignalComboBox, mapping.SourceSignal);
            SetComboText(TargetParameterComboBox, mapping.TargetParameter);
            SetComboText(ResponseCurveComboBox, mapping.ResponseCurve);
            SetComboText(GrammarComboBox, mapping.Grammar);
            GainNumberBox.Value = mapping.Gain;
            MappingSmoothingNumberBox.Value = mapping.Smoothing;
            ThresholdNumberBox.Value = mapping.Threshold;
            InputMinimumNumberBox.Value = mapping.InputMinimum;
            InputMaximumNumberBox.Value = mapping.InputMaximum;
            OutputMinimumNumberBox.Value = mapping.OutputMinimum;
            OutputMaximumNumberBox.Value = mapping.OutputMaximum;
            SetComboText(QuantizationComboBox, mapping.Quantization);
            SectionComboBox.SelectedItem = mapping.Section;
            CueComboBox.SelectedItem = mapping.Cue;
        }
        finally
        {
            _isUpdatingEditor = false;
        }
    }

    private void MoveSelectedMapping(int offset)
    {
        var from = MappingListView.SelectedIndex;
        var to = from + offset;
        if (from < 0 || to < 0 || to >= _mappings.Count)
        {
            return;
        }

        var reordered = ReactiveWorkflow.Move(_mappings, from, to);
        ReplaceMappings(reordered);
        MappingListView.SelectedIndex = to;
        PersistMappingChange();
    }

    private void ReplaceMappings(IEnumerable<ReactiveMapping> mappings)
    {
        _mappings.Clear();
        foreach (var mapping in mappings)
        {
            _mappings.Add(mapping);
        }

        MappingListView.SelectedIndex = _mappings.Count > 0 ? 0 : -1;
    }

    private async void PersistMappingChange()
    {
        UpdateDiagnostics();
        UpdateRawJson();
        await SaveLocalStateAsync();
    }

    private async Task<ReactiveLabLocalState?> LoadLocalStateAsync(string projectId)
    {
        var item = await ApplicationData.Current.LocalFolder.TryGetItemAsync(LocalStateFileName(projectId));
        if (item is not StorageFile file)
        {
            return null;
        }

        var json = await FileIO.ReadTextAsync(file);
        return JsonSerializer.Deserialize(json, StudioJsonContext.Default.ReactiveLabLocalState);
    }

    private async Task SaveLocalStateAsync()
    {
        if (string.IsNullOrWhiteSpace(_activeProjectId))
        {
            return;
        }

        _currentPreset = BuildCurrentPreset("Current");
        var state = new ReactiveLabLocalState
        {
            Current = _currentPreset,
            Presets = _savedPresets.ToList()
        };
        var file = await ApplicationData.Current.LocalFolder.CreateFileAsync(
            LocalStateFileName(_activeProjectId),
            CreationCollisionOption.ReplaceExisting);
        await FileIO.WriteTextAsync(
            file,
            JsonSerializer.Serialize(state, StudioJsonContext.Default.ReactiveLabLocalState));
    }

    private void UpdatePresetNames(string? selectedName = null)
    {
        _isLoadingPreset = true;
        try
        {
            _presetNames.Clear();
            foreach (var name in _savedPresets.Select(item => item.Name).OrderBy(name => name, StringComparer.OrdinalIgnoreCase))
            {
                _presetNames.Add(name);
            }

            PresetComboBox.SelectedItem = selectedName;
            DeletePresetButton.IsEnabled = PresetComboBox.SelectedItem is not null;
        }
        finally
        {
            _isLoadingPreset = false;
        }
    }

    private void UpdateRawJson()
    {
        _currentPreset = BuildCurrentPreset("Current");
        var request = new ReactiveLabApplyRequest
        {
            Metadata = BuildMetadata(_draftRequest.Metadata, _currentPreset),
            Keyframes = _draftRequest.Keyframes,
            BeatMarkers = _draftRequest.BeatMarkers,
            CueEvents = _draftRequest.CueEvents,
            Sections = _draftRequest.Sections,
            RepairSuggestions = _draftRequest.RepairSuggestions,
            Schedules = _draftRequest.Schedules,
            HandoffManifest = _draftRequest.HandoffManifest,
            ExtensionData = _draftRequest.ExtensionData,
            OverwriteMotionTrack = OverwriteMotionTrackToggle.IsOn,
            OverwriteCamera = OverwriteCameraToggle.IsOn,
            ExpectedRevision = StudioPageHelpers.ExpectedRevision(_project),
        };
        RawJsonTextBox.Text = FormatJson(JsonSerializer.Serialize(
            request,
            StudioJsonContext.Default.ReactiveLabApplyRequest));
    }

    private void UpdateDiagnostics()
    {
        var enabledCount = _mappings.Count(mapping => mapping.IsEnabled);
        var mappingErrors = _mappings
            .SelectMany((mapping, index) => ReactiveWorkflow.ValidateMapping(mapping)
                .Select(error => $"{index + 1}. {error}"))
            .ToList();
        DiagnosticsTextBlock.Text =
            $"{_mappings.Count} mappings · {enabledCount} enabled · " +
            $"{_draftRequest.Keyframes.Count} keyframes · {_draftRequest.CueEvents.Count} cues · {_draftRequest.Sections.Count} sections" +
            (mappingErrors.Count == 0 ? "\nMapping validation passed." : $"\n{string.Join("\n", mappingErrors)}");
    }

    private void UpdatePlanSummary(ProjectDto? project = null)
    {
        ProjectDto? selectedProject = project ?? _projects.FirstOrDefault(item => item.Id == _activeProjectId);
        if (selectedProject is null ||
            selectedProject.Meta.ValueKind != JsonValueKind.Object ||
            !selectedProject.Meta.TryGetProperty("last_plan", out var planElement))
        {
            VariantSummaryTextBlock.Text = "No selected plan variant.";
            return;
        }

        var plan = JsonSerializer.Deserialize(planElement.GetRawText(), StudioJsonContext.Default.PlanDto);
        if (plan is null || plan.Variants.Count == 0)
        {
            VariantSummaryTextBlock.Text = "No selected plan variant.";
            return;
        }

        _selectedVariantIndex = Math.Clamp(App.Services.Session.SelectedVariantIndex, 0, plan.Variants.Count - 1);
        var variant = plan.Variants[_selectedVariantIndex];
        VariantSummaryTextBlock.Text = $"{variant.DisplayName} · {variant.SceneCount} scenes" +
                                       (string.IsNullOrWhiteSpace(variant.Logline) ? string.Empty : $"\n{variant.Logline}");
    }

    private void SelectProject(string? projectId)
    {
        _isSynchronizingProject = true;
        try
        {
            ProjectComboBox.SelectedItem = _projects.FirstOrDefault(project => project.Id == projectId);
        }
        finally
        {
            _isSynchronizingProject = false;
        }
    }

    private void SynchronizeSessionProject(string? projectId)
    {
        _isSynchronizingProject = true;
        try
        {
            App.Services.Session.ActiveProjectId = projectId ?? string.Empty;
        }
        finally
        {
            _isSynchronizingProject = false;
        }
    }

    private void ClearContext()
    {
        _project = null;
        _musicGraph = null;
        _liveCues = null;
        _liveAssets = null;
        _timeline = default;
        VariantSummaryTextBlock.Text = "No selected plan variant.";
        MusicGraphSummaryTextBlock.Text = "No Music Graph loaded.";
        LiveCuesSummaryTextBlock.Text = "No live cues loaded.";
        LiveAssetsSummaryTextBlock.Text = "No live assets loaded.";
        TimelineSummaryTextBlock.Text = "No Timeline loaded.";
        SectionComboBox.ItemsSource = Array.Empty<string>();
        CueComboBox.ItemsSource = Array.Empty<string>();
        ReplaceMappings([]);
        RawJsonTextBox.Text = string.Empty;
        DiagnosticsTextBlock.Text = "Select a project to inspect Reactive Lab diagnostics.";
    }

    private async Task RunOperationAsync(
        string title,
        Func<CancellationToken, Task> operation,
        string? successMessage = null)
    {
        _operationCancellation?.Cancel();
        _operationCancellation?.Dispose();
        _operationCancellation = new CancellationTokenSource();
        SetBusy(true);
        ShowStatus(InfoBarSeverity.Informational, title, "Working…");
        try
        {
            await operation(_operationCancellation.Token);
            if (!string.IsNullOrWhiteSpace(successMessage))
            {
                ShowStatus(InfoBarSeverity.Success, "Reactive Lab updated", successMessage);
            }
        }
        catch (OperationCanceledException) when (_operationCancellation.IsCancellationRequested)
        {
            ShowStatus(InfoBarSeverity.Warning, "Operation canceled", "No additional Reactive Lab changes were requested.");
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict, _operationCancellation.Token);
        }
        catch (StudioApiException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.UserFacingMessage);
        }
        catch (HttpRequestException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.Message);
        }
        catch (JsonException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.Message);
        }
        catch (InvalidOperationException ex)
        {
            ShowStatus(InfoBarSeverity.Error, $"{title} failed", ex.Message);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void SetBusy(bool isBusy)
    {
        OperationProgressBar.Visibility = isBusy ? Visibility.Visible : Visibility.Collapsed;
        CancelButton.IsEnabled = isBusy;
        RefreshButton.IsEnabled = !isBusy;
        ApplyButton.IsEnabled = !isBusy;
        ProjectComboBox.IsEnabled = !isBusy;
    }

    private void ShowStatus(InfoBarSeverity severity, string title, string message)
    {
        StatusInfoBar.Severity = severity;
        StatusInfoBar.Title = title;
        StatusInfoBar.Message = message;
        StatusInfoBar.IsOpen = true;
    }

    private void ShowValidationErrors(IReadOnlyList<string> errors) =>
        ShowStatus(InfoBarSeverity.Warning, "Reactive payload needs attention", string.Join(" ", errors));

    private static ReactiveLabApplyRequest ParseRequest(string json) =>
        JsonSerializer.Deserialize(json, StudioJsonContext.Default.ReactiveLabApplyRequest)
        ?? throw new JsonException("The JSON did not contain a Reactive Lab payload.");

    private static IReadOnlyList<ReactiveMapping> ReadMappingsFromMetadata(JsonElement? metadata)
    {
        if (metadata?.ValueKind != JsonValueKind.Object)
        {
            return [];
        }

        foreach (var name in new[] { "mappings", "native_mappings" })
        {
            if (metadata.Value.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array)
            {
                return JsonSerializer.Deserialize(value.GetRawText(), StudioJsonContext.Default.ListReactiveMapping) ?? [];
            }
        }

        return [];
    }

    private static string SummarizeTimeline(JsonElement timeline)
    {
        if (timeline.ValueKind != JsonValueKind.Object)
        {
            return "Timeline response is empty.";
        }

        var fields = timeline.EnumerateObject().Count();
        var keyframes = ArrayLength(timeline, "keyframes");
        var beats = ArrayLength(timeline, "beat_markers");
        var cues = ArrayLength(timeline, "cue_events");
        var sections = ArrayLength(timeline, "sections");
        return $"{fields} fields · {keyframes} keyframes · {beats} beats · {cues} cues · {sections} sections";
    }

    private static int ArrayLength(JsonElement value, string propertyName) =>
        value.TryGetProperty(propertyName, out var property) && property.ValueKind == JsonValueKind.Array
            ? property.GetArrayLength()
            : 0;

    private static string BuildProjectSummary(ProjectDto project) =>
        $"{project.Name} · {(project.HasAudio ? project.AudioFileName : "no audio")} · " +
        $"{(project.Bpm is double bpm ? $"{bpm:0.#} BPM" : "tempo unavailable")} · " +
        $"{project.SectionCount} analyzed sections";

    private string SelectedProjectName() =>
        _projects.FirstOrDefault(project => project.Id == _activeProjectId)?.Name ?? "project";

    private static void InitializePicker(object picker)
    {
        var window = App.MainWindowInstance
                     ?? throw new InvalidOperationException("The main window is not available.");
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
    }

    private static string FormatJson(string json)
    {
        using var document = JsonDocument.Parse(json);
        return JsonSerializer.Serialize(document.RootElement, _indentedJsonContext.JsonElement);
    }

    private static string ComboText(ComboBox comboBox) =>
        NullIfWhiteSpace(comboBox.Text) ?? comboBox.SelectedItem?.ToString() ?? string.Empty;

    private static void SetComboText(ComboBox comboBox, string value)
    {
        comboBox.SelectedItem = comboBox.Items.Cast<object>()
            .FirstOrDefault(item => string.Equals(item?.ToString(), value, StringComparison.OrdinalIgnoreCase));
        if (comboBox.SelectedItem is null)
        {
            comboBox.Text = value;
        }
    }

    private static List<string> ParseList(string value) =>
        value.Split([',', '\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

    private static double FiniteValue(double value, double fallback) =>
        double.IsFinite(value) ? value : fallback;

    private static string? NullIfWhiteSpace(string? value) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim();

    private static string SafeFileName(string value) =>
        string.Concat(value.Select(character => Path.GetInvalidFileNameChars().Contains(character) ? '-' : character));

    private static string LocalStateFileName(string projectId) =>
        $"reactive-lab-{SafeFileName(projectId)}.json";
}
