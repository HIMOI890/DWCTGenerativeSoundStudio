using System.Text.Json;
using System.Text.Json.Nodes;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using EdmgStudio.WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class SettingsPage : Page
{
    private readonly EdmgStudio.Core.Services.StudioApiClient _apiClient = App.Services.ApiClient;
    private JsonObject? _renderProviderSettings;
    private bool _initializingAppearance;

    public SettingsPage()
    {
        InitializeComponent();
        InitializeAppearance();
        Loaded += SettingsPage_Loaded;
    }

    private async void SettingsPage_Loaded(object sender, RoutedEventArgs e) => await RefreshAsync();
    private async void RefreshButton_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async Task RefreshAsync()
    {
        SetBusy(true);
        try
        {
            Task<string?> renderTask = ProbeAndApplyAsync(
                "Render routing",
                () => _apiClient.GetRenderProvidersAsync(),
                ApplyRenderProviderSettings);
            Task<string?> transcriptionTask = ProbeAndApplyAsync(
                "Transcription",
                () => _apiClient.GetTranscriptionSettingsAsync(),
                ApplyTranscriptionSettings);
            Task<string?> secretsTask = ProbeAndApplyAsync(
                "Secrets",
                () => _apiClient.GetSecretStatusAsync(),
                value => SecretStatusText.Text = StudioPageHelpers.FormatJson(value));
            Task<(string Text, string? Error)> readinessTask =
                ProbeTextAsync("READINESS", () => _apiClient.GetSystemReadinessAsync());
            Task<(string Text, string? Error)> hardwareTask =
                ProbeTextAsync("HARDWARE", () => _apiClient.GetHardwareAsync());
            Task<(string Text, string? Error)> metricsTask =
                ProbeTextAsync("METRICS", () => _apiClient.GetBaselineMetricsAsync());
            Task<(string Text, string? Error)> securityTask =
                ProbeTextAsync("SECURITY AND PREVIEW LIMITS", () => _apiClient.GetSecurityStatusAsync());
            await Task.WhenAll(renderTask, transcriptionTask, secretsTask, readinessTask, hardwareTask, metricsTask, securityTask);

            DiagnosticsTextBox.Text =
                $"{securityTask.Result.Text}{Environment.NewLine}{Environment.NewLine}" +
                $"{readinessTask.Result.Text}{Environment.NewLine}{Environment.NewLine}" +
                $"{hardwareTask.Result.Text}{Environment.NewLine}{Environment.NewLine}" +
                metricsTask.Result.Text;
            LoadFoundrySettings();

            string?[] failures =
            [
                renderTask.Result,
                transcriptionTask.Result,
                secretsTask.Result,
                readinessTask.Result.Error,
                hardwareTask.Result.Error,
                metricsTask.Result.Error,
                securityTask.Result.Error,
            ];
            string[] availableFailures = failures.Where(value => !string.IsNullOrWhiteSpace(value)).Select(value => value!).ToArray();
            ShowStatus(
                availableFailures.Length == 0
                    ? "Settings and diagnostics loaded."
                    : $"Settings loaded with unavailable probes: {string.Join(" | ", availableFailures)}",
                availableFailures.Length == 0 ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
        }
        catch (Exception exception)
        {
            ShowStatus(StudioPageHelpers.GetErrorMessage(exception), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private void ApplyRenderProviderSettings(JsonElement value)
    {
        JsonObject render = StudioPageHelpers.ToObject(value);
        _renderProviderSettings = (render["settings"] as JsonObject)?.DeepClone().AsObject() ?? new JsonObject();
        JsonObject? video = _renderProviderSettings["video"] as JsonObject;
        SelectComboValue(VideoRouteComboBox, video?["preference"]?.GetValue<string>() ?? "auto");
        PreferGpuCheckBox.IsChecked = video?["auto_prefer_gpu"]?.GetValue<bool?>() ?? true;
        CloudFallbackCheckBox.IsChecked = video?["cosmos_fallback"]?.GetValue<bool?>() ?? true;
    }

    private void ApplyTranscriptionSettings(JsonElement value)
    {
        JsonObject transcription = StudioPageHelpers.ToObject(value);
        JsonObject settings = transcription["settings"] as JsonObject ?? transcription;
        SelectComboValue(TranscriptionProviderComboBox, settings["provider"]?.GetValue<string>() ?? "faster_whisper");
        SelectComboValue(TranscriptionDeviceComboBox, settings["device"]?.GetValue<string>() ?? "auto");
        SelectComboValue(ComputeTypeComboBox, settings["compute_type"]?.GetValue<string>() ?? "auto");
        TranscriptionModelTextBox.Text = settings["model"]?.GetValue<string>() ?? "turbo";
    }

    private static async Task<string?> ProbeAndApplyAsync(
        string name,
        Func<Task<JsonElement>> loadAsync,
        Action<JsonElement> apply)
    {
        try
        {
            apply(await loadAsync());
            return null;
        }
        catch (Exception exception) when (
            exception is StudioApiException or HttpRequestException or JsonException)
        {
            return $"{name}: {StudioPageHelpers.GetErrorMessage(exception)}";
        }
    }

    private static async Task<(string Text, string? Error)> ProbeTextAsync(
        string name,
        Func<Task<JsonElement>> loadAsync)
    {
        try
        {
            return ($"{name}{Environment.NewLine}{StudioPageHelpers.FormatJson(await loadAsync())}", null);
        }
        catch (Exception exception) when (
            exception is StudioApiException or HttpRequestException or JsonException)
        {
            string error = StudioPageHelpers.GetErrorMessage(exception);
            return ($"{name}{Environment.NewLine}Unavailable: {error}", $"{name}: {error}");
        }
    }

    private void LoadFoundrySettings()
    {
        try
        {
            FoundryProjectSettings settings = BackendSettingsStore.LoadFoundrySettings();
            FoundryProjectTextBox.Text = settings.ProjectName;
            FoundrySubscriptionTextBox.Text = settings.SubscriptionName;
            FoundryEndpointTextBox.Text = settings.ProjectEndpoint.AbsoluteUri;
        }
        catch (Exception exception) when (
            exception is InvalidDataException or IOException or UnauthorizedAccessException or ArgumentException)
        {
            ShowStatus(exception.Message, InfoBarSeverity.Error);
        }
    }

    private void InitializeAppearance()
    {
        _initializingAppearance = true;
        string current = StudioAppearanceService.CurrentThemeId;
        AppearanceThemeComboBox.SelectedIndex = StudioAppearanceService.ThemeIds
            .Select((id, index) => (id, index))
            .First(item => item.id == current)
            .index;
        _initializingAppearance = false;
    }

    private void AppearanceThemeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_initializingAppearance ||
            AppearanceThemeComboBox.SelectedItem is not ComboBoxItem { Tag: string themeId })
        {
            return;
        }

        StudioAppearanceService.ApplyTheme(themeId, App.MainWindowInstance?.Content as FrameworkElement ?? this);
        ShowStatus($"Appearance changed to {themeId}.", InfoBarSeverity.Success);
    }

    private void SaveFoundryButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var settings = new FoundryProjectSettings(
                FoundryProjectTextBox.Text,
                FoundrySubscriptionTextBox.Text,
                new Uri(FoundryEndpointTextBox.Text.Trim(), UriKind.Absolute));
            BackendSettingsStore.SaveFoundrySettings(settings);
            LoadFoundrySettings();
            ShowStatus("Foundry project metadata saved.", InfoBarSeverity.Success);
        }
        catch (Exception exception) when (
            exception is InvalidDataException or IOException or UnauthorizedAccessException or ArgumentException)
        {
            ShowStatus(exception.Message, InfoBarSeverity.Error);
        }
    }

    private async void SaveRouteButton_Click(object sender, RoutedEventArgs e)
    {
        var route = VideoRouteComboBox.SelectedItem as string ?? "auto";
        var payload = _renderProviderSettings?.DeepClone().AsObject() ?? new JsonObject();
        var video = payload["video"] as JsonObject ?? new JsonObject();
        video["preference"] = route;
        video["auto_prefer_gpu"] = PreferGpuCheckBox.IsChecked == true;
        video["cosmos_fallback"] = CloudFallbackCheckBox.IsChecked == true;
        video["allow_proxy_renders"] = false;
        payload["video"] = video;
        await RunSaveAsync(() => _apiClient.SaveRenderProvidersAsync(payload), "Video provider settings saved. Proxy rendering remains disabled.");
    }

    private async void SaveTranscriptionButton_Click(object sender, RoutedEventArgs e)
    {
        var payload = new JsonObject
        {
            ["provider"] = TranscriptionProviderComboBox.SelectedItem as string ?? "faster_whisper",
            ["device"] = TranscriptionDeviceComboBox.SelectedItem as string ?? "auto",
            ["compute_type"] = ComputeTypeComboBox.SelectedItem as string ?? "auto",
            ["model"] = TranscriptionModelTextBox.Text.Trim()
        };
        await RunSaveAsync(() => _apiClient.SaveTranscriptionSettingsAsync(payload), "Transcription settings saved.");
    }

    private async void SaveSecretButton_Click(object sender, RoutedEventArgs e)
    {
        if (SecretNameComboBox.SelectedItem is not string name || string.IsNullOrWhiteSpace(SecretValueBox.Password))
        {
            ShowStatus("Choose a secret and enter its new value.", InfoBarSeverity.Warning);
            return;
        }
        await RunSaveAsync(() => _apiClient.SetSecretAsync(name, SecretValueBox.Password), "Secret updated securely.");
        SecretValueBox.Password = string.Empty;
    }

    private async void ClearSecretButton_Click(object sender, RoutedEventArgs e)
    {
        if (SecretNameComboBox.SelectedItem is not string name)
        {
            return;
        }

        var confirmation = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = "Clear secret?",
            Content = $"Clear the stored value for {name}? Features that use this credential will remain unavailable until it is saved again.",
            PrimaryButtonText = "Clear",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await confirmation.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        await RunSaveAsync(() => _apiClient.ClearSecretAsync(name), "Secret cleared.");
    }

    private async Task RunSaveAsync(Func<Task> operation, string successMessage)
    {
        SetBusy(true);
        try
        {
            await operation();
            ShowStatus(successMessage, InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            ShowStatus(StudioPageHelpers.GetErrorMessage(ex), InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private static void SelectComboValue(ComboBox comboBox, string value)
    {
        foreach (var item in comboBox.Items.OfType<string>())
        {
            if (string.Equals(item, value, StringComparison.OrdinalIgnoreCase))
            {
                comboBox.SelectedItem = item;
                return;
            }
        }
    }

    private void SetBusy(bool value)
    {
        BusyRing.IsActive = value;
        RefreshButton.IsEnabled = !value;
        SettingsScroller.IsEnabled = !value;
    }

    private void ShowStatus(string message, InfoBarSeverity severity)
    {
        StatusInfoBar.Message = message;
        StatusInfoBar.Severity = severity;
        StatusInfoBar.IsOpen = true;
    }
}
