using System.Collections.ObjectModel;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text.Json;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using EdmgStudio.WinUI.Services;
using Microsoft.Windows.AppLifecycle;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.System;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class SetupPage : Page, IStudioRefreshable
{
    private static readonly TimeSpan ActivePollInterval = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan IdlePollInterval = TimeSpan.FromSeconds(15);

    private readonly ObservableCollection<SetupTaskView> _tasks = [];
    private string _baseStorageStatus = string.Empty;
    private CancellationTokenSource? _lifetimeCts;
    private bool _refreshing;
    private bool _requestingAction;
    private bool _hadActiveTasks;

    public SetupPage()
    {
        InitializeComponent();
        SetupTasksItems.ItemsSource = _tasks;
        PopulateStaticConfiguration();
        Loaded += SetupPage_Loaded;
        Unloaded += SetupPage_Unloaded;
    }

    private void PopulateStaticConfiguration()
    {
        var configuration = App.Services.Configuration;
        var paths = configuration.Paths;
        StudioHomeText.Text = $"Studio home: {paths.StudioHome}";
        DataPathText.Text = $"Data: {paths.DataDirectory}";
        ModelsPathText.Text = $"Models: {paths.ModelsDirectory}";
        CachePathText.Text = $"Cache: {paths.CacheDirectory}";
        LogsPathText.Text = $"Logs: {paths.LogsDirectory}";
        _baseStorageStatus = paths.PreparationWarnings.Count == 0
            ? "Storage paths are ready."
            : string.Join(Environment.NewLine, paths.PreparationWarnings);
        StorageStatusText.Text = _baseStorageStatus;

        BackendModeText.Text = $"Mode: {configuration.Mode} · accelerator: {configuration.AcceleratorProfile}";
        BackendAddressText.Text = $"Address: {configuration.BackendUri}";
        BackendSourceText.Text =
            $"Configuration: {configuration.Source} · mode from {configuration.BackendModeSource} · address from {configuration.BackendAddressSource}";
        BackendValidationText.Text = configuration.ValidationErrors.Count == 0
            ? string.Empty
            : string.Join(Environment.NewLine, configuration.ValidationErrors);
    }

    public async Task RefreshAsync(CancellationToken cancellationToken = default)
    {
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(
            cancellationToken,
            _lifetimeCts?.Token ?? CancellationToken.None);
        _ = await RefreshPageAsync(refreshDiagnostics: true, linkedCts.Token);
    }

    private async void SetupPage_Loaded(object sender, RoutedEventArgs e)
    {
        _lifetimeCts?.Cancel();
        _lifetimeCts?.Dispose();
        var lifetimeCts = new CancellationTokenSource();
        _lifetimeCts = lifetimeCts;

        try
        {
            _ = await RefreshPageAsync(refreshDiagnostics: true, lifetimeCts.Token);
            _ = PollTasksAsync(lifetimeCts.Token);
        }
        catch (OperationCanceledException) when (lifetimeCts.IsCancellationRequested)
        {
        }
    }

    private void SetupPage_Unloaded(object sender, RoutedEventArgs e)
    {
        _lifetimeCts?.Cancel();
        _lifetimeCts?.Dispose();
        _lifetimeCts = null;
    }

    private async Task<bool> RefreshPageAsync(bool refreshDiagnostics, CancellationToken cancellationToken)
    {
        if (_refreshing)
        {
            return false;
        }

        _refreshing = true;
        await SetRefreshStateAsync(refreshing: true);

        try
        {
            BackendStatus snapshot = await RefreshBackendSnapshotAsync(cancellationToken);
            if (!snapshot.IsReady)
            {
                await ShowBackendHealthAsync(snapshot);
                return false;
            }

            var setup = await App.Services.ApiClient.GetSetupStatusAsync(
                refresh: refreshDiagnostics,
                includeOptional: true,
                cancellationToken: cancellationToken);
            await UpdateSetupStatusAsync(setup);
            await UpdateTasksAsync(setup.Tasks);
            return true;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync("Setup refresh", cancellationToken);
            throw;
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, "Setup failed");
            return false;
        }
        finally
        {
            _refreshing = false;
            await SetRefreshStateAsync(refreshing: false);
        }
    }

    private async Task PollTasksAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            var isActive = _hadActiveTasks;
            try
            {
                var response = await App.Services.ApiClient.GetSetupTasksAsync(cancellationToken);
                var becameIdle = _hadActiveTasks && !response.Active;
                _hadActiveTasks = response.Active;
                isActive = response.Active;
                await UpdateTasksAsync(response.Tasks);

                if (becameIdle)
                {
                    var setup = await App.Services.ApiClient.GetSetupStatusAsync(
                        refresh: true,
                        includeOptional: true,
                        cancellationToken: cancellationToken);
                    await UpdateSetupStatusAsync(setup);
                }
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                await ShowCancellationAsync("Task polling", cancellationToken);
                break;
            }
            catch (Exception exception)
            {
                await PresentFailureAsync(exception, cancellationToken, "Task refresh failed");
            }

            try
            {
                await Task.Delay(isActive ? ActivePollInterval : IdlePollInterval, cancellationToken);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                await ShowCancellationAsync("Task polling", cancellationToken);
                break;
            }
        }
    }

    private void UpdateBackendSnapshot(BackendStatus snapshot)
    {
        BackendReadinessText.Text = snapshot.State switch
        {
            BackendLifecycleState.Ready => $"Backend: connected to {snapshot.CurrentBackendUri}",
            BackendLifecycleState.Starting => "Backend: starting the managed runtime...",
            BackendLifecycleState.WaitingForHealth => "Backend: waiting for the health endpoint...",
            BackendLifecycleState.Unavailable => "Backend: unavailable",
            BackendLifecycleState.Failed => "Backend: failed to start",
            _ => $"Backend: {snapshot.State}",
        };
        BackendModeText.Text =
            $"Mode: {snapshot.Mode} · accelerator: {App.Services.Configuration.AcceleratorProfile} · owned process: {(snapshot.OwnsProcess ? "yes" : "no")}";
        BackendAddressText.Text = $"Address: {snapshot.CurrentBackendUri}";
        BackendValidationText.Text = snapshot.State is BackendLifecycleState.Failed or BackendLifecycleState.Unavailable
            ? snapshot.Detail ?? snapshot.Message
            : App.Services.Configuration.ValidationErrors.Count == 0
                ? string.Empty
                : string.Join(Environment.NewLine, App.Services.Configuration.ValidationErrors);
    }

    private void UpdateSetupStatus(SetupStatusResponse setup)
    {
        BundleReadinessText.Text =
            $"Backend bundle: {DescribeDiagnostic(setup.BackendBundle, "ok", "ready")} · toolchain: {DescribeDiagnostic(setup.Toolchain, "ready", "ok")}";
        FfmpegReadinessText.Text = "FFmpeg: " + DescribeReadinessSection(setup.SystemReadiness, "ffmpeg");
        AiReadinessText.Text = "AI path: " + DescribeDiagnostic(setup.AiConfig, "ok", "ready");
        ModelReadinessText.Text = "AI model: " + DescribeReadinessSection(setup.SystemReadiness, "model");
        ComfyReadinessText.Text = "ComfyUI: " + DescribeDiagnostic(setup.ComfyUi, "ok", "available", "running");
        EdmgReadinessText.Text = "EDMG Core: " + DescribeDiagnostic(setup.Edmg, "available", "ok");
        SevenZipReadinessText.Text = "7-Zip: " + DescribeDiagnostic(setup.SevenZip, "ok", "available");

        var profile = ReadString(setup.Toolchain, "accelerator_profile")
            ?? ReadString(setup.Toolchain, "profile");
        SelectTaggedItem(AcceleratorProfileComboBox, profile);

        var model = ReadString(setup.Ollama, "model");
        if (!string.IsNullOrWhiteSpace(model))
        {
            OllamaModelTextBox.Text = model;
        }

        var storageSummary = BuildStorageSummary(setup);
        StorageStatusText.Text = storageSummary.StartsWith(
            "Storage paths are managed",
            StringComparison.Ordinal)
            ? _baseStorageStatus
            : $"{_baseStorageStatus}{Environment.NewLine}{storageSummary}";
    }

    private void UpdateTasks(IEnumerable<SetupTaskDto> tasks)
    {
        var ordered = tasks
            .OrderByDescending(task => task.IsActive)
            .ThenByDescending(task => task.StartedAt ?? 0)
            .Select(task => new SetupTaskView(task))
            .ToList();

        _tasks.Clear();
        foreach (var task in ordered)
        {
            _tasks.Add(task);
        }

        var activeCount = ordered.Count(task => task.CanCancel);
        TasksSummaryText.Text = ordered.Count == 0
            ? "No installer tasks have been reported."
            : activeCount > 0
                ? $"{activeCount} active of {ordered.Count} reported task(s). Active tasks refresh every second."
                : $"{ordered.Count} completed task(s). Idle polling runs every 15 seconds.";
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await RefreshAsync();
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async void RefreshTasks_Click(object sender, RoutedEventArgs e)
    {
        await RefreshTasksNowAsync();
    }

    private async Task RefreshTasksNowAsync()
    {
        var cancellationToken = _lifetimeCts?.Token ?? CancellationToken.None;
        try
        {
            var response = await App.Services.ApiClient.GetSetupTasksAsync(cancellationToken);
            _hadActiveTasks = response.Active;
            await UpdateTasksAsync(response.Tasks);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync("Task refresh", cancellationToken);
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, "Task refresh failed");
        }
    }

    private async void RetryBackend_Click(object sender, RoutedEventArgs e)
    {
        await RunSupervisorActionAsync(
            "Backend start requested.",
            cancellationToken => App.Services.BackendSupervisor.StartAsync(cancellationToken));
    }

    private async Task RunSupervisorActionAsync(
        string successMessage,
        Func<CancellationToken, Task> action)
    {
        var cancellationToken = _lifetimeCts?.Token ?? CancellationToken.None;
        try
        {
            await ClearStatusAsync();
            await action(cancellationToken);
            bool refreshed = await RefreshPageAsync(refreshDiagnostics: false, cancellationToken);
            if (refreshed)
            {
                await ShowSuccessAsync(successMessage);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync("Backend action", cancellationToken);
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, "Backend action failed");
        }
    }

    private async void SaveToken_Click(object sender, RoutedEventArgs e)
    {
        var token = BackendTokenBox.Password.Trim();

        try
        {
            WindowsBackendTokenProvider.Save(string.IsNullOrWhiteSpace(token) ? null : token);
            await ClearTokenBoxAsync();
            await ShowSuccessAsync(string.IsNullOrWhiteSpace(token)
                ? "The saved backend token was cleared. Environment-provided tokens are unchanged. New requests use the updated credential state immediately."
                : "The backend token was stored securely. New Studio requests use it immediately.");
        }
        catch (InvalidOperationException ex)
        {
            await ShowErrorAsync(ex.Message);
        }
    }

    private async void ClearToken_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            WindowsBackendTokenProvider.Save(null);
            await ClearTokenBoxAsync();
            await ShowSuccessAsync("The saved backend token was cleared. Environment-provided tokens are unchanged. New requests use the updated credential state immediately.");
        }
        catch (InvalidOperationException ex)
        {
            await ShowErrorAsync(ex.Message);
        }
    }

    private async void ResetBackend_Click(object sender, RoutedEventArgs e)
    {
        var configuration = App.Services.Configuration;
        var confirmation = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Reset to the managed Python backend?",
            Content =
                "Studio will replace only the saved backend target with http://127.0.0.1:7863. " +
                "Storage, models, accelerator settings, Foundry configuration, and other bootstrap settings are preserved.\n\n" +
                $"Current target: {configuration.ConfiguredBackendUrl ?? configuration.BackendUri.ToString()}",
            PrimaryButtonText = "Reset and restart",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await confirmation.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        try
        {
            BackendSettingsStore.ResetToManaged();
            var restartResult = AppInstance.Restart("--backend-reset");
            if (!string.Equals(restartResult.ToString(), "RestartPending", StringComparison.Ordinal))
            {
                await ShowErrorAsync($"The target was reset, but Studio could not restart ({restartResult}). Close and reopen Studio.");
            }
        }
        catch (InvalidOperationException ex)
        {
            await ShowErrorAsync(ex.Message);
        }
    }

    private async void OpenStudioHome_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var folder = await StorageFolder.GetFolderFromPathAsync(App.Services.Configuration.Paths.StudioHome);
            await Launcher.LaunchFolderAsync(folder);
        }
        catch (Exception ex) when (ex is FileNotFoundException or DirectoryNotFoundException or UnauthorizedAccessException)
        {
            await ShowErrorAsync(ex.Message);
        }
    }

    private void OpenWorkspace_Click(object sender, RoutedEventArgs e)
    {
        App.Services.Session.SetLastWorkflowDestination("workspace");
        App.Navigate("workspace");
    }

    private async void InstallFullSetup_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "Full setup",
            token => App.Services.ApiClient.InstallFullSetupAsync(
                GetSelectedTag(AcceleratorProfileComboBox, "cpu"),
                GetComfyPort(),
                OllamaModelTextBox.Text,
                token));

    private async void InstallBackend_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "Backend profile synchronization",
            token => App.Services.ApiClient.InstallBackendAsync(
                GetSelectedTag(AcceleratorProfileComboBox, "cpu"),
                token));

    private async void InstallSevenZip_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync("7-Zip installation", App.Services.ApiClient.InstallSevenZipAsync);

    private async void InstallEdmg_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "EDMG Core installation",
            token => App.Services.ApiClient.InstallEdmgCoreAsync(
                backend: GetSelectedTag(AcceleratorProfileComboBox, "cpu"),
                cancellationToken: token));

    private async void InstallOllama_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync("Managed Ollama installation", App.Services.ApiClient.InstallManagedOllamaAsync);

    private async void DownloadRunOllama_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync("Ollama download and run", App.Services.ApiClient.DownloadAndRunOllamaAsync);

    private async void StartOllama_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync("Managed Ollama start", App.Services.ApiClient.StartManagedOllamaAsync);

    private async void PullOllamaModel_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "Ollama model pull",
            token => App.Services.ApiClient.PullOllamaModelAsync(OllamaModelTextBox.Text, token));

    private async void InstallComfyUi_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "Portable ComfyUI installation",
            token => App.Services.ApiClient.InstallPortableComfyUiAsync(
                GetSelectedTag(ComfyFlavorComboBox, "cpu"),
                token));

    private async void StartComfyUi_Click(object sender, RoutedEventArgs e) =>
        await QueueSetupActionAsync(
            "Portable ComfyUI start",
            token => App.Services.ApiClient.StartPortableComfyUiAsync(
                "auto",
                GetComfyPort(),
                token));

    private async void StopComfyUi_Click(object sender, RoutedEventArgs e)
    {
        if (_requestingAction)
        {
            return;
        }

        var cancellationToken = _lifetimeCts?.Token ?? CancellationToken.None;
        _requestingAction = true;
        await SetActionsEnabledAsync(enabled: false);
        await ClearStatusAsync();
        try
        {
            var response = await App.Services.ApiClient.StopPortableComfyUiAsync(cancellationToken);
            if (!response.Ok)
            {
                throw new InvalidOperationException("The backend did not confirm that ComfyUI was stopped.");
            }

            await ShowSuccessAsync("Portable ComfyUI stop requested.");
            var setup = await App.Services.ApiClient.GetSetupStatusAsync(
                refresh: true,
                includeOptional: true,
                cancellationToken: cancellationToken);
            await UpdateSetupStatusAsync(setup);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync("Portable ComfyUI stop", cancellationToken);
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, "Portable ComfyUI stop failed");
        }
        finally
        {
            await SetActionsEnabledAsync(enabled: true);
            _requestingAction = false;
        }
    }

    private async Task QueueSetupActionAsync(
        string actionName,
        Func<CancellationToken, Task<SetupTaskActionResponse>> action)
    {
        if (_requestingAction)
        {
            return;
        }

        var cancellationToken = _lifetimeCts?.Token ?? CancellationToken.None;
        _requestingAction = true;
        await SetActionsEnabledAsync(enabled: false);
        await ClearStatusAsync();
        try
        {
            var response = await action(cancellationToken);
            if (!response.Ok || string.IsNullOrWhiteSpace(response.Task.Id))
            {
                throw new InvalidOperationException($"The backend did not queue {actionName.ToLowerInvariant()}.");
            }

            _hadActiveTasks = response.Task.IsActive;
            await ShowSuccessAsync($"{actionName} queued as task {response.Task.Id}.");
            await RefreshTasksNowAsync();
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync(actionName, cancellationToken);
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, $"{actionName} failed");
        }
        finally
        {
            await SetActionsEnabledAsync(enabled: true);
            _requestingAction = false;
        }
    }

    private async void CancelTask_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string taskId } || string.IsNullOrWhiteSpace(taskId))
        {
            return;
        }

        var cancellationToken = _lifetimeCts?.Token ?? CancellationToken.None;
        try
        {
            var response = await App.Services.ApiClient.CancelSetupTaskAsync(taskId, cancellationToken);
            if (!response.Ok)
            {
                throw new InvalidOperationException($"The backend did not accept cancellation for task {taskId}.");
            }

            await ShowSuccessAsync($"Cancellation requested for {response.Task.Name}.");
            await RefreshTasksNowAsync();
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            await ShowCancellationAsync($"Cancellation for task {taskId}", cancellationToken);
        }
        catch (Exception exception)
        {
            await PresentFailureAsync(exception, cancellationToken, "Task cancellation failed");
        }
    }

    private int GetComfyPort()
    {
        if (double.IsNaN(ComfyPortNumberBox.Value)
            || ComfyPortNumberBox.Value < 1
            || ComfyPortNumberBox.Value > 65535)
        {
            throw new ArgumentOutOfRangeException(
                nameof(ComfyPortNumberBox),
                "ComfyUI port must be between 1 and 65535.");
        }

        return checked((int)ComfyPortNumberBox.Value);
    }

    private static string GetSelectedTag(ComboBox comboBox, string fallback) =>
        comboBox.SelectedItem is ComboBoxItem { Tag: string tag } && !string.IsNullOrWhiteSpace(tag)
            ? tag
            : fallback;

    private static void SelectTaggedItem(ComboBox comboBox, string? tag)
    {
        if (string.IsNullOrWhiteSpace(tag))
        {
            return;
        }

        foreach (var item in comboBox.Items.OfType<ComboBoxItem>())
        {
            if (string.Equals(item.Tag as string, tag, StringComparison.OrdinalIgnoreCase))
            {
                comboBox.SelectedItem = item;
                return;
            }
        }
    }

    private static string DescribeReadinessSection(JsonElement readiness, string name)
    {
        if (readiness.ValueKind != JsonValueKind.Object
            || !readiness.TryGetProperty(name, out var section))
        {
            return "not reported";
        }

        return DescribeDiagnostic(section, "ok", "ready", "available", "present");
    }

    private static string DescribeDiagnostic(JsonElement section, params string[] flags)
    {
        if (section.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null)
        {
            return "not reported";
        }

        if (section.ValueKind == JsonValueKind.True)
        {
            return "ready";
        }

        if (section.ValueKind == JsonValueKind.False)
        {
            return "needs attention";
        }

        if (section.ValueKind != JsonValueKind.Object)
        {
            return section.ToString();
        }

        foreach (var flag in flags)
        {
            if (section.TryGetProperty(flag, out var value)
                && value.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                return value.GetBoolean() ? "ready" : "needs attention";
            }
        }

        return ReadString(section, "status")
            ?? ReadString(section, "hint")
            ?? "reported";
    }

    private static string? ReadString(JsonElement section, string name) =>
        section.ValueKind == JsonValueKind.Object
        && section.TryGetProperty(name, out var value)
        && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string BuildStorageSummary(SetupStatusResponse setup)
    {
        var candidates = new[]
        {
            ReadString(setup.Ollama, "managed_models_dir"),
            ReadString(setup.ComfyUi, "install_dir"),
            ReadString(setup.Edmg, "install_dir"),
        }.Where(value => !string.IsNullOrWhiteSpace(value)).ToArray();

        return candidates.Length == 0
            ? "Storage paths are managed by the shell and were not reported by setup diagnostics."
            : string.Join(Environment.NewLine, candidates);
    }

    private async Task<BackendStatus> RefreshBackendSnapshotAsync(CancellationToken cancellationToken)
    {
        BackendStatus snapshot = await App.Services.BackendSupervisor.RefreshHealthAsync(cancellationToken);
        await UpdateBackendSnapshotAsync(snapshot);
        return snapshot;
    }

    private Task UpdateBackendSnapshotAsync(BackendStatus snapshot)
        => RunOnDispatcherAsync(() => UpdateBackendSnapshot(snapshot));

    private Task UpdateSetupStatusAsync(SetupStatusResponse setup)
        => RunOnDispatcherAsync(() => UpdateSetupStatus(setup));

    private Task UpdateTasksAsync(IEnumerable<SetupTaskDto> tasks)
    {
        var snapshot = tasks.ToArray();
        return RunOnDispatcherAsync(() => UpdateTasks(snapshot));
    }

    private Task SetRefreshStateAsync(bool refreshing)
        => RunOnDispatcherAsync(() =>
        {
            RefreshButton.IsEnabled = !refreshing;
            SetupProgress.IsActive = refreshing;
            if (refreshing)
            {
                SetupInfoBar.IsOpen = false;
                BackendReadinessText.Text = "Backend: checking...";
            }
        });

    private Task SetActionsEnabledAsync(bool enabled)
        => RunOnDispatcherAsync(() => StudioPageHelpers.SetControlsEnabled(ActionsCard, enabled));

    private Task ClearStatusAsync() => RunOnDispatcherAsync(() => SetupInfoBar.IsOpen = false);

    private Task ClearTokenBoxAsync() => RunOnDispatcherAsync(() => BackendTokenBox.Password = string.Empty);

    private async Task PresentFailureAsync(Exception exception, CancellationToken cancellationToken, string defaultTitle)
    {
        if (TryCreateAuthenticationPresentation(exception, out var authentication))
        {
            await ShowStatusAsync(authentication.Severity, authentication.Title, authentication.Message);
            return;
        }

        BackendStatus? snapshot = null;
        if (exception is StudioApiException || IsTransportFailure(exception))
        {
            snapshot = await RefreshBackendSnapshotAsync(cancellationToken);
            if (!snapshot.IsReady)
            {
                await ShowBackendHealthAsync(snapshot);
                return;
            }
        }

        if (IsTransportFailure(exception))
        {
            await ShowStatusAsync(
                InfoBarSeverity.Warning,
                "Connection failed",
                $"{StudioPageHelpers.GetErrorMessage(exception)} Check that {(snapshot ?? App.Services.BackendSupervisor.Status).CurrentBackendUri} is reachable.");
            return;
        }

        await ShowStatusAsync(InfoBarSeverity.Error, defaultTitle, StudioPageHelpers.GetErrorMessage(exception));
    }

    private Task ShowBackendHealthAsync(BackendStatus snapshot)
    {
        var title = snapshot.State switch
        {
            BackendLifecycleState.Resolving or
            BackendLifecycleState.CheckingExisting or
            BackendLifecycleState.Starting or
            BackendLifecycleState.WaitingForHealth => "Backend starting",
            BackendLifecycleState.Failed => "Backend failed",
            _ => "Backend unavailable"
        };
        var severity = snapshot.State switch
        {
            BackendLifecycleState.Resolving or
            BackendLifecycleState.CheckingExisting or
            BackendLifecycleState.Starting or
            BackendLifecycleState.WaitingForHealth => InfoBarSeverity.Informational,
            BackendLifecycleState.Failed => InfoBarSeverity.Error,
            _ => InfoBarSeverity.Warning
        };
        return ShowStatusAsync(severity, title, snapshot.Detail ?? snapshot.Message);
    }

    private Task ShowCancellationAsync(string operationName, CancellationToken cancellationToken)
    {
        if (!cancellationToken.IsCancellationRequested || _lifetimeCts?.IsCancellationRequested == true)
        {
            return Task.CompletedTask;
        }

        return ShowStatusAsync(InfoBarSeverity.Informational, "Canceled", $"{operationName} was canceled.");
    }

    private Task ShowSuccessAsync(string message)
        => ShowStatusAsync(InfoBarSeverity.Success, "Setup", message);

    private Task ShowErrorAsync(string message)
        => ShowStatusAsync(InfoBarSeverity.Error, "Setup failed", message);

    private Task ShowStatusAsync(InfoBarSeverity severity, string title, string message)
        => RunOnDispatcherAsync(() => ApplyStatus(severity, title, message));

    private void ShowSuccess(string message) => ShowStatus(InfoBarSeverity.Success, "Setup", message);

    private void ShowError(string message) => ShowStatus(InfoBarSeverity.Error, "Setup failed", message);

    private void ShowStatus(InfoBarSeverity severity, string title, string message)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            ApplyStatus(severity, title, message);
            return;
        }

        _ = DispatcherQueue.TryEnqueue(() => ApplyStatus(severity, title, message));
    }

    private void ApplyStatus(InfoBarSeverity severity, string title, string message)
    {
        SetupInfoBar.Severity = severity;
        SetupInfoBar.Title = title;
        SetupInfoBar.Message = message;
        SetupInfoBar.IsOpen = true;
    }

    private static bool TryCreateAuthenticationPresentation(
        Exception exception,
        out SetupStatusPresentation presentation)
    {
        if (exception is StudioApiException apiException
            && (apiException.AuthenticationChallenge
                || apiException.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden))
        {
            presentation = new SetupStatusPresentation(
                InfoBarSeverity.Warning,
                "Authentication required",
                string.IsNullOrWhiteSpace(apiException.Hint)
                    ? $"{apiException.Message} Save a backend token on this page if the backend requires one."
                    : apiException.UserFacingMessage);
            return true;
        }

        presentation = default;
        return false;
    }

    private static bool IsTransportFailure(Exception exception)
    {
        for (Exception? current = exception; current is not null; current = current.InnerException)
        {
            if (current is HttpRequestException or IOException or SocketException)
            {
                return true;
            }
        }

        return false;
    }

    private Task RunOnDispatcherAsync(Action action)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            action();
            return Task.CompletedTask;
        }

        var completion = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!DispatcherQueue.TryEnqueue(() =>
            {
                try
                {
                    action();
                    completion.SetResult();
                }
                catch (Exception exception)
                {
                    completion.SetException(exception);
                }
            }))
        {
            completion.SetResult();
        }

        return completion.Task;
    }

    private readonly record struct SetupStatusPresentation(
        InfoBarSeverity Severity,
        string Title,
        string Message);
}

public sealed class SetupTaskView
{
    public SetupTaskView(SetupTaskDto task)
    {
        Id = task.Id;
        Name = string.IsNullOrWhiteSpace(task.Name) ? "Setup task" : task.Name;
        CanCancel = task.IsActive && !task.CancelRequested;
        ProgressPercent = Math.Clamp((task.Progress ?? 0) * 100, 0, 100);
        StatusText = task.CancelRequested
            ? $"{task.Status} · cancellation requested"
            : task.Status;
        DetailText = !string.IsNullOrWhiteSpace(task.Error)
            ? task.Error
            : !string.IsNullOrWhiteSpace(task.LastLog)
                ? task.LastLog
                : "No task log is available.";
        CancelAutomationId = "CancelSetupTask_" + SanitizeAutomationId(task.Id);
        ProgressAutomationName = string.Create(
            CultureInfo.InvariantCulture,
            $"{Name} progress {ProgressPercent:0} percent");
    }

    public string Id { get; }

    public string Name { get; }

    public string StatusText { get; }

    public string DetailText { get; }

    public double ProgressPercent { get; }

    public bool CanCancel { get; }

    public string CancelAutomationId { get; }

    public string ProgressAutomationName { get; }

    private static string SanitizeAutomationId(string value)
    {
        var characters = value
            .Select(character => char.IsLetterOrDigit(character) ? character : '_')
            .ToArray();
        return characters.Length == 0 ? "Unknown" : new string(characters);
    }
}
