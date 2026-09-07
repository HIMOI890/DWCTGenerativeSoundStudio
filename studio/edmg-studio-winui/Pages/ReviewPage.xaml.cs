using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Text.Json;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class ReviewPage : Page, INotifyPropertyChanged
{
    private readonly StudioApiClient _apiClient = App.Services.ApiClient;
    private readonly StudioProjectMediaClient _projectMediaClient = App.Services.ProjectMediaClient;
    private readonly List<string> _selectedPaths = [];
    private CancellationTokenSource? _pageCancellation;
    private CancellationTokenSource? _previewCancellation;
    private DispatcherQueueTimer? _pollTimer;
    private ProjectDto? _selectedProject;
    private ReviewArtifact? _primaryArtifact;
    private ReviewJobItem? _selectedJob;
    private bool _isBusy;
    private bool _isPolling;
    private bool _isRestoringSelection;
    private int _previewGeneration;

    public ReviewPage()
    {
        InitializeComponent();
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public ObservableCollection<ReviewArtifact> Artifacts { get; } = [];

    public ObservableCollection<ReviewArtifact> SelectedArtifacts { get; } = [];

    public ObservableCollection<ReviewContinuityWarning> ContinuityWarnings { get; } = [];

    public ObservableCollection<ReviewJobItem> Jobs { get; } = [];

    public ReviewJobItem? SelectedJob
    {
        get => _selectedJob;
        private set => SetField(ref _selectedJob, value);
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        _pageCancellation?.Cancel();
        _pageCancellation?.Dispose();
        _pageCancellation = new CancellationTokenSource();

        _pollTimer = DispatcherQueue.CreateTimer();
        _pollTimer.Interval = TimeSpan.FromSeconds(2.5);
        _pollTimer.Tick += PollTimer_Tick;
        _pollTimer.Start();

        await LoadProjectsAsync(_pageCancellation.Token);
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        if (_pollTimer is not null)
        {
            _pollTimer.Stop();
            _pollTimer.Tick -= PollTimer_Tick;
            _pollTimer = null;
        }

        CancelPreview();
        _pageCancellation?.Cancel();
        _pageCancellation?.Dispose();
        _pageCancellation = null;
    }

    private async Task LoadProjectsAsync(CancellationToken cancellationToken)
    {
        SetBusy(true);
        try
        {
            ProjectListResponse response = await _apiClient.GetProjectsAsync(cancellationToken);
            ProjectComboBox.ItemsSource = response.Projects;

            string activeProjectId = App.Services.Session.ActiveProjectId;
            ProjectDto? project = response.Projects.FirstOrDefault(item =>
                string.Equals(item.Id, activeProjectId, StringComparison.OrdinalIgnoreCase))
                ?? response.Projects.FirstOrDefault();

            if (project is null)
            {
                ResetSurface();
                ShowStatus("No projects", "Create or open a project before reviewing artifacts.", InfoBarSeverity.Warning);
                return;
            }

            _isRestoringSelection = true;
            ProjectComboBox.SelectedItem = project;
            _isRestoringSelection = false;
            await SelectProjectAsync(project, cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Projects could not be loaded", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task SelectProjectAsync(ProjectDto project, CancellationToken cancellationToken)
    {
        bool isRestoringProject = string.Equals(
            project.Id,
            App.Services.Session.ActiveProjectId,
            StringComparison.OrdinalIgnoreCase);
        int desiredVariant = isRestoringProject ? App.Services.Session.SelectedVariantIndex : 0;

        _selectedProject = project;
        App.Services.Session.ActiveProjectId = project.Id;
        App.Services.Session.SelectedVariantIndex = Math.Max(0, desiredVariant);
        await RefreshSurfaceAsync(cancellationToken, showSuccess: false);
    }

    private async Task RefreshSurfaceAsync(CancellationToken cancellationToken, bool showSuccess)
    {
        if (_selectedProject is null)
        {
            return;
        }

        string projectId = _selectedProject.Id;
        var failures = new List<string>();
        SetBusy(true);
        try
        {
            try
            {
                await LoadReviewAsync(projectId, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                failures.Add($"Review: {StudioPageHelpers.GetErrorMessage(ex)}");
            }

            try
            {
                await LoadContinuityAsync(projectId, App.Services.Session.SelectedVariantIndex, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                failures.Add($"Continuity: {StudioPageHelpers.GetErrorMessage(ex)}");
            }

            try
            {
                await LoadJobsAsync(projectId, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                failures.Add($"Jobs: {StudioPageHelpers.GetErrorMessage(ex)}");
            }

            try
            {
                await LoadPublishingAsync(projectId, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                failures.Add($"Publishing: {StudioPageHelpers.GetErrorMessage(ex)}");
            }

            cancellationToken.ThrowIfCancellationRequested();
            if (!string.Equals(_selectedProject?.Id, projectId, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            if (failures.Count > 0)
            {
                ShowStatus(
                    "Review refreshed with warnings",
                    string.Join(Environment.NewLine, failures),
                    InfoBarSeverity.Warning);
            }
            else if (showSuccess)
            {
                ShowStatus("Review refreshed", "Artifacts, continuity, jobs, and publishing status are current.", InfoBarSeverity.Success);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task LoadReviewAsync(string projectId, CancellationToken cancellationToken)
    {
        JsonElement response = await _apiClient.GetVariantReviewAsync(projectId, cancellationToken);
        JsonElement review = TryGetObject(response, "variant_review", out JsonElement wrapper)
            ? wrapper
            : response;

        var loaded = new List<ReviewArtifact>();
        if (review.ValueKind == JsonValueKind.Object &&
            review.TryGetProperty("groups", out JsonElement groups) &&
            groups.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement group in groups.EnumerateArray())
            {
                int groupVariant = ReadInt(group, "variant_index");
                string groupLabel = ReadString(group, "label", $"Variant {groupVariant + 1}");
                string groupMood = ReadString(group, "mood");
                if (!group.TryGetProperty("artifacts", out JsonElement artifactArray) ||
                    artifactArray.ValueKind != JsonValueKind.Array)
                {
                    continue;
                }

                foreach (JsonElement artifact in artifactArray.EnumerateArray())
                {
                    ReviewArtifact item = ReviewArtifact.FromJson(artifact, groupVariant, groupLabel, groupMood);
                    if (!string.IsNullOrWhiteSpace(item.Path))
                    {
                        loaded.Add(item);
                    }
                }
            }
        }

        int variantCount = Math.Max(
            1,
            ReadInt(
                review,
                "plan_variant_count",
                loaded.Count == 0 ? 1 : loaded.Max(item => item.VariantIndex) + 1));
        int desiredVariant = Math.Clamp(App.Services.Session.SelectedVariantIndex, 0, variantCount - 1);

        _isRestoringSelection = true;
        VariantComboBox.ItemsSource = Enumerable.Range(0, variantCount)
            .Select(index => new VariantOption(index, $"Variant {index + 1}"))
            .ToArray();
        VariantComboBox.SelectedIndex = desiredVariant;
        _isRestoringSelection = false;
        App.Services.Session.SelectedVariantIndex = desiredVariant;

        Artifacts.Clear();
        foreach (ReviewArtifact artifact in loaded)
        {
            Artifacts.Add(artifact);
        }

        IReadOnlyList<string> availableSelection = StudioReviewSelection.KeepAvailable(
            _selectedPaths,
            Artifacts.Select(item => item.Path));
        ReplaceSelectedPaths(availableSelection);

        string? sessionArtifact = App.Services.Session.SelectedArtifactPath;
        if (_selectedPaths.Count == 0 &&
            !string.IsNullOrWhiteSpace(sessionArtifact) &&
            Artifacts.Any(item => string.Equals(item.Path, sessionArtifact, StringComparison.OrdinalIgnoreCase)))
        {
            ReplaceSelectedPaths(StudioReviewSelection.AddRecent(_selectedPaths, sessionArtifact));
        }

        RestoreArtifactSelection();
        await UpdateSelectionPresentationAsync(cancellationToken);

        int artifactCount = ReadInt(review, "artifact_count", Artifacts.Count);
        bool compareReady = ReadBoolean(review, "compare_ready", artifactCount > 1);
        ReviewSummaryText.Text =
            $"{artifactCount} artifact{(artifactCount == 1 ? string.Empty : "s")} · Compare ready: {(compareReady ? "yes" : "no")}";
    }

    private async Task LoadContinuityAsync(
        string projectId,
        int variantIndex,
        CancellationToken cancellationToken)
    {
        JsonElement response = await _apiClient.GetRenderConductorContinuityAsync(
            projectId,
            variantIndex,
            cancellationToken);
        JsonElement continuity = TryGetObject(response, "continuity", out JsonElement wrapper)
            ? wrapper
            : response;

        var warnings = new List<ReviewContinuityWarning>();
        if (continuity.ValueKind == JsonValueKind.Object &&
            continuity.TryGetProperty("warnings", out JsonElement warningArray) &&
            warningArray.ValueKind == JsonValueKind.Array)
        {
            foreach (JsonElement warning in warningArray.EnumerateArray())
            {
                string code = ReadString(warning, "code", "continuity_warning");
                string severity = ReadString(warning, "severity", "warning");
                string sceneId = ReadString(warning, "scene_id");
                string message = ReadString(warning, "message", "Continuity attention is required.");
                warnings.Add(new ReviewContinuityWarning(
                    $"{TitleCase(severity)} · {TitleCase(code)}",
                    $"{(string.IsNullOrWhiteSpace(sceneId) ? "Project-wide" : $"Scene {sceneId}")}: {message}"));
            }
        }

        ContinuityWarnings.Clear();
        foreach (ReviewContinuityWarning warning in warnings)
        {
            ContinuityWarnings.Add(warning);
        }

        int warningCount = ReadInt(continuity, "warning_count", warnings.Count);
        int blockingCount = ReadInt(continuity, "blocking_count");
        bool isReady = ReadBoolean(continuity, "ok_to_render", warningCount == 0);
        ContinuitySummaryText.Text =
            $"{warningCount} warning{(warningCount == 1 ? string.Empty : "s")} · " +
            $"Blocking: {blockingCount} · Ready to render: {(isReady ? "yes" : "no")}";
    }

    private async Task LoadJobsAsync(string projectId, CancellationToken cancellationToken)
    {
        string? desiredJobId = SelectedJob?.Job.Id;
        if (string.IsNullOrWhiteSpace(desiredJobId) &&
            string.Equals(App.Services.Session.SelectedJobProjectId, projectId, StringComparison.OrdinalIgnoreCase))
        {
            desiredJobId = App.Services.Session.SelectedJobId;
        }

        StudioJobListResponse response = await _apiClient.GetProjectJobsAsync(projectId, cancellationToken);
        Jobs.Clear();
        foreach (StudioJob job in response.Jobs.OrderByDescending(item => item.UpdatedAt ?? item.CreatedAt))
        {
            Jobs.Add(new ReviewJobItem(job));
        }

        ReviewJobItem? selected = Jobs.FirstOrDefault(item =>
            string.Equals(item.Job.Id, desiredJobId, StringComparison.OrdinalIgnoreCase));
        JobsList.SelectedItem = selected;
        SelectedJob = selected;
        JobsSummaryText.Text = Jobs.Count == 0
            ? "No project render jobs."
            : $"{Jobs.Count} job{(Jobs.Count == 1 ? string.Empty : "s")} · {Jobs.Count(item => item.Job.IsActive)} active";
        UpdateJobCommands();
    }

    private async Task LoadPublishingAsync(string projectId, CancellationToken cancellationToken)
    {
        LiveCuePublishResponse response =
            await _apiClient.GetTypedLiveCuePublishStatusAsync(projectId, cancellationToken);
        UpdatePublishingStatus(response.Publish);
    }

    private async Task UpdateSelectionPresentationAsync(CancellationToken cancellationToken)
    {
        SelectedArtifacts.Clear();
        foreach (string path in _selectedPaths)
        {
            ReviewArtifact? artifact = Artifacts.FirstOrDefault(item =>
                string.Equals(item.Path, path, StringComparison.OrdinalIgnoreCase));
            if (artifact is not null)
            {
                SelectedArtifacts.Add(artifact);
            }
        }

        _primaryArtifact = SelectedArtifacts.LastOrDefault();
        int selectedCount = SelectedArtifacts.Count;
        SelectionSummaryText.Text = selectedCount == 0
            ? "Select up to four artifacts."
            : $"{selectedCount} of {StudioReviewSelection.MaximumComparisonArtifacts} selected.";

        bool hasSelection = selectedCount > 0;
        ApproveButton.IsEnabled = hasSelection && !_isBusy;
        CherryPickButton.IsEnabled = hasSelection && !_isBusy;
        RejectButton.IsEnabled = hasSelection && !_isBusy;

        if (_primaryArtifact is null || _selectedProject is null)
        {
            App.Services.Session.SetSelectedArtifact(null);
            App.Services.Session.SetSourceAsset(null);
            CancelPreview();
            ArtifactPreview.ShowEmpty("Select an artifact to preview it here.");
            PreviewTitleText.Text = "Select an artifact.";
            ArtifactMetadataText.Text = "No artifact selected.";
            return;
        }

        App.Services.Session.SetSelectedArtifact(_primaryArtifact.Path);
        App.Services.Session.SetSourceAsset(_primaryArtifact.Path);
        App.Services.Session.SelectedVariantIndex = _primaryArtifact.VariantIndex;
        PreviewTitleText.Text = _primaryArtifact.Name;
        ArtifactMetadataText.Text = _primaryArtifact.Metadata;
        await LoadPreviewAsync(_selectedProject.Id, _primaryArtifact, cancellationToken);
    }

    private async Task LoadPreviewAsync(
        string projectId,
        ReviewArtifact artifact,
        CancellationToken pageToken)
    {
        CancelPreview();
        int generation = ++_previewGeneration;
        _previewCancellation = CancellationTokenSource.CreateLinkedTokenSource(pageToken);
        CancellationToken cancellationToken = _previewCancellation.Token;

        if (!artifact.IsImage && !artifact.IsVideo)
        {
            ArtifactPreview.ShowUnsupported("This artifact does not have an inline preview.");
            return;
        }

        try
        {
            await _projectMediaClient.StreamProjectMediaAsync(
                projectId,
                artifact.Path,
                async (file, callbackToken) =>
                {
                    callbackToken.ThrowIfCancellationRequested();
                    if (generation != Volatile.Read(ref _previewGeneration))
                    {
                        return false;
                    }

                    if (artifact.IsVideo)
                    {
                        await ArtifactPreview.LoadVideoStreamAsync(file.Stream, file.ContentHeaders.ContentLength, callbackToken);
                    }
                    else
                    {
                        await ArtifactPreview.LoadStreamAsync(
                            file.Stream,
                            file.ContentHeaders.ContentType?.MediaType,
                            callbackToken);
                    }

                    return true;
                },
                cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            if (generation != _previewGeneration)
            {
                return;
            }

            ArtifactPreview.ShowError("Preview failed to load.");
            ShowStatus("Preview failed", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
    }

    private void CancelPreview()
    {
        _previewCancellation?.Cancel();
        _previewCancellation?.Dispose();
        _previewCancellation = null;
    }

    private async Task ApplyDecisionAsync(string decision)
    {
        if (_selectedProject is null || _selectedPaths.Count == 0 || _isBusy)
        {
            ShowStatus("Nothing selected", "Select at least one artifact to review.", InfoBarSeverity.Warning);
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        string[] selectedPaths = [.. _selectedPaths];
        string[] traits = TraitsTextBox.Text
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        SetBusy(true);
        try
        {
            foreach (string path in selectedPaths)
            {
                await _apiClient.SaveVariantDecisionAsync(
                    _selectedProject.Id,
                    new VariantReviewDecisionRequest
                    {
                        ArtifactPath = path,
                        Decision = decision,
                        Notes = string.IsNullOrWhiteSpace(NotesTextBox.Text) ? null : NotesTextBox.Text.Trim(),
                        CherryPickTraits = decision == "cherry_picked" ? traits : [],
                        LockFields = decision == "approved" ? ["timing", "reference"] : []
                    },
                    cancellationToken);
            }

            ReplaceSelectedPaths([]);
            RestoreArtifactSelection();
            await LoadReviewAsync(_selectedProject.Id, cancellationToken);
            ShowStatus(
                "Editorial decision saved",
                $"{TitleCase(decision)} applied to {selectedPaths.Length} artifact{(selectedPaths.Length == 1 ? string.Empty : "s")}.",
                InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Decision could not be saved", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task RunJobActionAsync(
        string action,
        Func<string, string, CancellationToken, Task<StudioJobActionResponse>> operation,
        StudioJobConfirmationAction? confirmationAction = null)
    {
        if (_selectedProject is null || SelectedJob is null || _isBusy)
        {
            return;
        }

        if (confirmationAction is StudioJobConfirmationAction requiredConsent &&
            !await StudioPageHelpers.ConfirmAsync(
                XamlRoot,
                StudioJobConfirmationFactory.CreateRecoveryConsent(SelectedJob.Job, requiredConsent)))
        {
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            StudioJobActionResponse response = await operation(
                _selectedProject.Id,
                SelectedJob.Job.Id,
                cancellationToken);
            await LoadJobsAsync(_selectedProject.Id, cancellationToken);
            ShowStatus($"{action} requested", $"Job {response.Job.Id} is {response.Job.Status}.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus($"{action} failed", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task LoadSelectedJobLogAsync()
    {
        if (_selectedProject is null || SelectedJob is null)
        {
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        try
        {
            JsonElement detail = await _apiClient.GetProjectJobAsync(
                _selectedProject.Id,
                SelectedJob.Job.Id,
                80,
                cancellationToken);
            string log = ReadString(detail, "log_tail");
            if (string.IsNullOrWhiteSpace(log) &&
                detail.TryGetProperty("tail", out JsonElement tail))
            {
                log = tail.ValueKind == JsonValueKind.String
                    ? tail.GetString() ?? string.Empty
                    : tail.GetRawText();
            }

            JobLogTextBox.Text = string.IsNullOrWhiteSpace(log)
                ? StudioPageHelpers.FormatJson(detail)
                : log;
            JobLogTextBox.Visibility = Visibility.Visible;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            JobLogTextBox.Text = StudioPageHelpers.GetErrorMessage(ex);
            JobLogTextBox.Visibility = Visibility.Visible;
        }
    }

    private async Task StartPublishingAsync()
    {
        if (_selectedProject is null || _isBusy)
        {
            return;
        }

        string host = OscHostTextBox.Text.Trim();
        int port = (int)OscPortNumberBox.Value;
        if (string.IsNullOrWhiteSpace(host) || port is < 1 or > 65535)
        {
            ShowStatus("Invalid publisher target", "Enter an OSC host and a port from 1 through 65535.", InfoBarSeverity.Warning);
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            LiveCuePublishResponse response = await _apiClient.StartLiveCuePublishAsync(
                _selectedProject.Id,
                new LiveCuePublishRequest
                {
                    OscHost = host,
                    OscPort = port,
                    MidiEnabled = true,
                    WebsocketEnabled = true,
                    PlaybackSpeed = 1.0
                },
                cancellationToken);
            UpdatePublishingStatus(response.Publish);
            ShowStatus("Publishing started", "Live cues are being published to the configured transports.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Publishing failed", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task StopPublishingAsync()
    {
        if (_selectedProject is null || _isBusy)
        {
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            LiveCuePublishResponse response =
                await _apiClient.StopLiveCuePublishAsync(_selectedProject.Id, cancellationToken);
            UpdatePublishingStatus(response.Publish);
            ShowStatus("Publishing stopped", "Live cue transport output is stopped.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Publishing could not be stopped", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task ExportAdapterAsync(string adapter)
    {
        if (_selectedProject is null || _isBusy)
        {
            return;
        }

        CancellationToken cancellationToken = _pageCancellation?.Token ?? CancellationToken.None;
        SetBusy(true);
        try
        {
            WorldAdapterExportResponse response = await _apiClient.ExportWorldAdapterAsync(
                _selectedProject.Id,
                new WorldAdapterExportRequest
                {
                    Adapter = adapter,
                    VariantIndex = App.Services.Session.SelectedVariantIndex,
                    SequenceName = "EDMG_LiveSet"
                },
                cancellationToken);
            int simulatedEvents = ReadInt(response.Simulation, "simulated_events");
            ShowStatus(
                "Adapter exported",
                $"{adapter} adapter export is ready ({simulatedEvents} simulated event{(simulatedEvents == 1 ? string.Empty : "s")}).",
                InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Export failed", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void UpdatePublishingStatus(JsonElement publish)
    {
        bool isRunning = ReadBoolean(publish, "running", ReadBoolean(publish, "active", false));
        int sentCount = ReadInt(publish, "sent_count", ReadInt(publish, "events_sent"));
        string target = ReadString(publish, "osc_target");
        if (string.IsNullOrWhiteSpace(target))
        {
            string host = ReadString(publish, "host");
            int port = ReadInt(publish, "port");
            target = string.IsNullOrWhiteSpace(host) ? "not configured" : $"{host}:{port}";
        }

        PublishStatusText.Text =
            $"{(isRunning ? "Publishing" : "Stopped")} · Sent {sentCount} · OSC {target}";
    }

    private void ReplaceSelectedPaths(IEnumerable<string> paths)
    {
        _selectedPaths.Clear();
        _selectedPaths.AddRange(paths);
    }

    private void RestoreArtifactSelection()
    {
        _isRestoringSelection = true;
        ArtifactList.SelectedItems.Clear();
        foreach (ReviewArtifact artifact in Artifacts)
        {
            if (_selectedPaths.Any(path =>
                string.Equals(path, artifact.Path, StringComparison.OrdinalIgnoreCase)))
            {
                ArtifactList.SelectedItems.Add(artifact);
            }
        }
        _isRestoringSelection = false;
    }

    private void UpdateJobCommands()
    {
        PauseJobButton.IsEnabled = !_isBusy && SelectedJob is { Job.CanPause: true } or { Job.CanResume: true };
        CancelJobButton.IsEnabled = !_isBusy && SelectedJob?.Job.CanCancel == true;
        RetryJobButton.IsEnabled = !_isBusy && SelectedJob?.Job.CanRetry == true;
        ViewJobLogButton.IsEnabled = !_isBusy && SelectedJob is not null;
    }

    private void SetBusy(bool value)
    {
        _isBusy = value;
        BusyProgressBar.Visibility = value ? Visibility.Visible : Visibility.Collapsed;
        ProjectComboBox.IsEnabled = !value;
        VariantComboBox.IsEnabled = !value;
        StartPublishButton.IsEnabled = !value;
        StopPublishButton.IsEnabled = !value;
        ExportTouchDesignerButton.IsEnabled = !value;
        ExportUnrealButton.IsEnabled = !value;
        bool hasSelection = _selectedPaths.Count > 0;
        ApproveButton.IsEnabled = !value && hasSelection;
        CherryPickButton.IsEnabled = !value && hasSelection;
        RejectButton.IsEnabled = !value && hasSelection;
        UpdateJobCommands();
    }

    private void ResetSurface()
    {
        _selectedProject = null;
        Artifacts.Clear();
        SelectedArtifacts.Clear();
        ContinuityWarnings.Clear();
        Jobs.Clear();
        ReplaceSelectedPaths([]);
        SelectedJob = null;
        ReviewSummaryText.Text = "Select a project to review.";
        SelectionSummaryText.Text = "Select up to four artifacts.";
        ContinuitySummaryText.Text = "Run analysis and generate a plan to populate continuity checks.";
        JobsSummaryText.Text = "No jobs loaded.";
        PublishStatusText.Text = "Publishing status unavailable.";
        JobLogTextBox.Visibility = Visibility.Collapsed;
        ArtifactPreview.ShowEmpty("Select an artifact to preview it here.");
    }

    private async void OnProjectSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isRestoringSelection || ProjectComboBox.SelectedItem is not ProjectDto project)
        {
            return;
        }

        await SelectProjectAsync(project, _pageCancellation?.Token ?? CancellationToken.None);
    }

    private async void OnVariantSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isRestoringSelection ||
            _selectedProject is null ||
            VariantComboBox.SelectedItem is not VariantOption option)
        {
            return;
        }

        App.Services.Session.SelectedVariantIndex = option.Index;
        try
        {
            await LoadContinuityAsync(
                _selectedProject.Id,
                option.Index,
                _pageCancellation?.Token ?? CancellationToken.None);
        }
        catch (Exception ex)
        {
            ShowStatus("Continuity could not be refreshed", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
    }

    private async void OnArtifactSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isRestoringSelection)
        {
            return;
        }

        IEnumerable<string> remaining = _selectedPaths.Where(path =>
            !e.RemovedItems.OfType<ReviewArtifact>().Any(item =>
                string.Equals(item.Path, path, StringComparison.OrdinalIgnoreCase)));
        IReadOnlyList<string> updated = remaining.ToArray();
        foreach (ReviewArtifact added in e.AddedItems.OfType<ReviewArtifact>())
        {
            updated = StudioReviewSelection.AddRecent(updated, added.Path);
        }

        ReplaceSelectedPaths(updated);
        RestoreArtifactSelection();
        await UpdateSelectionPresentationAsync(_pageCancellation?.Token ?? CancellationToken.None);
    }

    private async void OnRefreshClick(object sender, RoutedEventArgs e) =>
        await RefreshSurfaceAsync(_pageCancellation?.Token ?? CancellationToken.None, showSuccess: true);

    private async void OnApproveClick(object sender, RoutedEventArgs e) =>
        await ApplyDecisionAsync("approved");

    private async void OnCherryPickClick(object sender, RoutedEventArgs e) =>
        await ApplyDecisionAsync("cherry_picked");

    private async void OnRejectClick(object sender, RoutedEventArgs e) =>
        await ApplyDecisionAsync("rejected");

    private void OnJobSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        SelectedJob = JobsList.SelectedItem as ReviewJobItem;
        App.Services.Session.SetSelectedJob(_selectedProject?.Id, SelectedJob?.Job.Id);
        JobLogTextBox.Visibility = Visibility.Collapsed;
        UpdateJobCommands();
    }

    private async void OnPauseResumeJobClick(object sender, RoutedEventArgs e)
    {
        if (SelectedJob?.Job.CanResume == true)
        {
            await RunJobActionAsync("Resume", _apiClient.ResumeJobAsync, StudioJobConfirmationAction.Resume);
        }
        else if (SelectedJob?.Job.CanPause == true)
        {
            await RunJobActionAsync("Pause", _apiClient.PauseJobAsync);
        }
    }

    private async void OnCancelJobClick(object sender, RoutedEventArgs e) =>
        await RunJobActionAsync("Cancel", _apiClient.CancelJobAsync);

    private async void OnRetryJobClick(object sender, RoutedEventArgs e) =>
        await RunJobActionAsync("Retry", _apiClient.RetryJobAsync, StudioJobConfirmationAction.Retry);

    private async void OnViewJobLogClick(object sender, RoutedEventArgs e) =>
        await LoadSelectedJobLogAsync();

    private async void OnStartPublishClick(object sender, RoutedEventArgs e) =>
        await StartPublishingAsync();

    private async void OnStopPublishClick(object sender, RoutedEventArgs e) =>
        await StopPublishingAsync();

    private async void OnExportTouchDesignerClick(object sender, RoutedEventArgs e) =>
        await ExportAdapterAsync("touchdesigner");

    private async void OnExportUnrealClick(object sender, RoutedEventArgs e) =>
        await ExportAdapterAsync("unreal");

    private void OnOpenOutputsClick(object sender, RoutedEventArgs e) => NavigateTo("outputs");

    private void OnOpenQueueClick(object sender, RoutedEventArgs e) => NavigateTo("queue");

    private void OnOpenTimelineClick(object sender, RoutedEventArgs e) => NavigateTo("timeline");

    private void OnOpenRenderClick(object sender, RoutedEventArgs e)
    {
        if (_primaryArtifact is not null)
        {
            App.Services.Session.SetRenderContext(_primaryArtifact.Path);
        }

        NavigateTo("render");
    }

    private void NavigateTo(string destination)
    {
        if (_primaryArtifact is not null)
        {
            App.Services.Session.SetSelectedArtifact(_primaryArtifact.Path);
            App.Services.Session.SetSourceAsset(_primaryArtifact.Path);
            App.Services.Session.SelectedVariantIndex = _primaryArtifact.VariantIndex;
        }

        App.Services.Session.SetLastWorkflowDestination(destination);
        App.Navigate(destination);
    }

    private async void PollTimer_Tick(DispatcherQueueTimer sender, object args)
    {
        CancellationToken cancellationToken = _pageCancellation?.Token ?? new CancellationToken(canceled: true);
        if (_isPolling || _isBusy || _selectedProject is null || cancellationToken.IsCancellationRequested)
        {
            return;
        }

        string projectId = _selectedProject.Id;
        _isPolling = true;
        try
        {
            await LoadJobsAsync(projectId, cancellationToken);
            await LoadPublishingAsync(projectId, cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ShowStatus("Background refresh paused", StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Warning);
        }
        finally
        {
            _isPolling = false;
        }
    }

    private void ShowStatus(string title, string message, InfoBarSeverity severity)
    {
        StatusBar.Title = title;
        StatusBar.Message = message;
        StatusBar.Severity = severity;
        StatusBar.IsOpen = true;
    }

    internal static bool TryGetObject(JsonElement element, string propertyName, out JsonElement value)
    {
        if (element.ValueKind == JsonValueKind.Object &&
            element.TryGetProperty(propertyName, out value) &&
            value.ValueKind == JsonValueKind.Object)
        {
            return true;
        }

        value = default;
        return false;
    }

    internal static string ReadString(JsonElement element, string propertyName, string fallback = "")
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out JsonElement value))
        {
            return fallback;
        }

        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString() ?? fallback,
            JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False => value.ToString(),
            _ => fallback
        };
    }

    internal static int ReadInt(JsonElement element, string propertyName, int fallback = 0)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out JsonElement value))
        {
            return fallback;
        }

        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int number))
        {
            return number;
        }

        return int.TryParse(value.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out number)
            ? number
            : fallback;
    }

    internal static long? ReadLong(JsonElement element, string propertyName)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out JsonElement value))
        {
            return null;
        }

        return value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out long number)
            ? number
            : null;
    }

    internal static double? ReadDouble(JsonElement element, string propertyName)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out JsonElement value))
        {
            return null;
        }

        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out double number)
            ? number
            : null;
    }

    private static bool ReadBoolean(JsonElement element, string propertyName, bool fallback)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(propertyName, out JsonElement value))
        {
            return fallback;
        }

        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => fallback
        };
    }

    internal static string TitleCase(string value) =>
        CultureInfo.CurrentCulture.TextInfo.ToTitleCase(value.Replace('_', ' '));

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }
}

public sealed record VariantOption(int Index, string Label);

public sealed class ReviewArtifact
{
    public ReviewArtifact()
    {
    }

        public string Path { get; set; } = string.Empty;

        public string Name { get; set; } = string.Empty;

        public string Kind { get; set; } = string.Empty;

        public int VariantIndex { get; set; }

        public string VariantLabel { get; set; } = string.Empty;

        public string Mood { get; set; } = string.Empty;

        public string ReviewState { get; set; } = string.Empty;

        public string ReviewNotes { get; set; } = string.Empty;

        public string Engine { get; set; } = string.Empty;

        public string ModelId { get; set; } = string.Empty;

        public string Seed { get; set; } = string.Empty;

        public IReadOnlyList<string> Traits { get; set; } = [];

        public IReadOnlyList<string> Locks { get; set; } = [];

        public string Provenance { get; set; } = string.Empty;

        public long? SizeBytes { get; set; }

        public double? ModifiedAt { get; set; }

        public bool IsVideo =>
            string.Equals(Kind, "video", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".mp4", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".webm", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".mov", StringComparison.OrdinalIgnoreCase);

        public bool IsImage =>
            string.Equals(Kind, "image", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".png", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".jpeg", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".webp", StringComparison.OrdinalIgnoreCase) ||
            Path.EndsWith(".bmp", StringComparison.OrdinalIgnoreCase);

        public string ComparisonSummary =>
            $"{VariantLabel} · {ReviewPage.TitleCase(ReviewState)} · " +
            $"{(string.IsNullOrWhiteSpace(Engine) ? "engine unavailable" : Engine)}";

        public string Metadata
        {
            get
            {
                var parts = new List<string>
                {
                    Path,
                    $"{Kind} · {FormatFileSize(SizeBytes)}",
                    $"{VariantLabel}{(string.IsNullOrWhiteSpace(Mood) ? string.Empty : $" · {Mood}")}",
                    $"State: {ReviewPage.TitleCase(ReviewState)}"
                };
                if (!string.IsNullOrWhiteSpace(ReviewNotes))
                {
                    parts.Add($"Notes: {ReviewNotes}");
                }
                if (Traits.Count > 0)
                {
                    parts.Add($"Traits: {string.Join(", ", Traits)}");
                }
                if (Locks.Count > 0)
                {
                    parts.Add($"Locks: {string.Join(", ", Locks)}");
                }
                parts.Add(Provenance);
                return string.Join(Environment.NewLine, parts);
            }
        }

        public static ReviewArtifact FromJson(
            JsonElement artifact,
            int groupVariantIndex,
            string groupLabel,
            string groupMood)
        {
            int variantIndex = ReviewPage.ReadInt(artifact, "variant_index", groupVariantIndex);
            return new ReviewArtifact
            {
                Path = ReviewPage.ReadString(artifact, "path"),
                Name = ReviewPage.ReadString(artifact, "name", ReviewPage.ReadString(artifact, "path")),
                Kind = ReviewPage.ReadString(artifact, "kind", "artifact"),
                VariantIndex = variantIndex,
                VariantLabel = string.IsNullOrWhiteSpace(groupLabel)
                    ? $"Variant {variantIndex + 1}"
                    : groupLabel,
                Mood = groupMood,
                ReviewState = ReviewPage.ReadString(artifact, "review_state", "unreviewed"),
                ReviewNotes = ReviewPage.ReadString(artifact, "review_notes"),
                Engine = ReviewPage.ReadString(artifact, "engine"),
                ModelId = ReviewPage.ReadString(artifact, "model_id"),
                Seed = ReviewPage.ReadString(artifact, "seed"),
                Traits = ReadStringArray(artifact, "cherry_pick_traits"),
                Locks = ReadStringArray(artifact, "locks"),
                Provenance = FormatProvenance(artifact),
                SizeBytes = ReviewPage.ReadLong(artifact, "size_bytes"),
                ModifiedAt = ReviewPage.ReadDouble(artifact, "modified_at")
            };
        }

        private static IReadOnlyList<string> ReadStringArray(JsonElement element, string propertyName)
        {
            if (!element.TryGetProperty(propertyName, out JsonElement values) ||
                values.ValueKind != JsonValueKind.Array)
            {
                return [];
            }

            return values.EnumerateArray()
                .Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => item.GetString())
                .Where(item => !string.IsNullOrWhiteSpace(item))
                .Cast<string>()
                .ToArray();
        }

        private static string FormatProvenance(JsonElement artifact)
        {
            var parts = new List<string>();
            string hash = ReviewPage.ReadString(artifact, "content_hash");
            if (!string.IsNullOrWhiteSpace(hash))
            {
                parts.Add($"Hash {hash[..Math.Min(hash.Length, 12)]}");
            }

            if (ReviewPage.TryGetObject(artifact, "provenance", out JsonElement provenance))
            {
                int parentCount = ArrayCount(provenance, "parents");
                int sourceCount = ArrayCount(provenance, "source_assets");
                if (parentCount > 0)
                {
                    parts.Add($"{parentCount} parent{(parentCount == 1 ? string.Empty : "s")}");
                }
                if (sourceCount > 0)
                {
                    parts.Add($"{sourceCount} source asset{(sourceCount == 1 ? string.Empty : "s")}");
                }
            }

            return parts.Count == 0 ? "No provenance metadata." : string.Join(" · ", parts);
        }

        private static int ArrayCount(JsonElement element, string propertyName) =>
            element.TryGetProperty(propertyName, out JsonElement values) &&
            values.ValueKind == JsonValueKind.Array
                ? values.GetArrayLength()
                : 0;

        private static string FormatFileSize(long? size)
        {
            if (size is null || size < 0)
            {
                return "Unknown size";
            }

            string[] units = ["B", "KB", "MB", "GB"];
            double value = size.Value;
            int unit = 0;
            while (value >= 1024 && unit < units.Length - 1)
            {
                value /= 1024;
                unit++;
            }

            return $"{value:0.#} {units[unit]}";
        }
}

public sealed class ReviewContinuityWarning
{
    public ReviewContinuityWarning(string heading, string detail)
    {
        Heading = heading;
        Detail = detail;
    }

    public string Heading { get; set; }

    public string Detail { get; set; }
}

public sealed class ReviewJobItem
{
    public ReviewJobItem(StudioJob job)
    {
        Job = job;
    }

    public StudioJob Job { get; }

    public string Title => $"{(string.IsNullOrWhiteSpace(Job.Type) ? "Job" : ReviewPage.TitleCase(Job.Type))} · {Job.Id}";

    public string Status => ReviewPage.TitleCase(Job.Status);

    public string Error => Job.Error ?? string.Empty;

    public string ProgressSummary
    {
        get
        {
            string percent = Job.Progress?.Percent is double value
                ? $"{Math.Clamp(value, 0, 100):0.#}%"
                : "Progress unavailable";
            string detail = Job.Progress?.Message ?? Job.Progress?.Stage ?? $"Attempt {Job.Attempt}";
            return $"{percent} · {detail}";
        }
    }
}
