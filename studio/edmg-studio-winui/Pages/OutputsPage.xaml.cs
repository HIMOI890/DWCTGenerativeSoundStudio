using System.Collections.ObjectModel;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using EdmgStudio.WinUI.Controls;
using EdmgStudio.WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.System;
using WinRT.Interop;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class OutputsPage : Page
{
    private readonly StudioApiClient _apiClient = App.Services.ApiClient;
    private readonly StudioProjectMediaClient _projectMediaClient = App.Services.ProjectMediaClient;
    private readonly StudioSessionService _session = App.Services.Session;
    private readonly BackendConfiguration _backendConfiguration = App.Services.Configuration;
    private CancellationTokenSource? _previewCts;
    private string? _previewTempPath;

    public OutputsPage()
    {
        InitializeComponent();
    }

    public ObservableCollection<StudioOutputItem> Items { get; } = [];

    public ObservableCollection<StudioOutputItem> VisibleItems { get; } = [];

    private string ActiveProjectId => _session.ActiveProjectId;

    private StudioOutputItem? SelectedOutput => OutputsList.SelectedItem as StudioOutputItem;

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await RefreshAsync();
    }

    protected override async void OnNavigatedFrom(NavigationEventArgs e)
    {
        await CancelPreviewAsync(clearSurface: true);
        base.OnNavigatedFrom(e);
    }

    private async Task RefreshAsync(string? preferredStableIdentity = null)
    {
        string? selectionIdentity = preferredStableIdentity ?? SelectedOutput?.StableIdentity;
        if (string.IsNullOrWhiteSpace(ActiveProjectId))
        {
            Items.Clear();
            VisibleItems.Clear();
            await CancelPreviewAsync(clearSurface: true);
            SetStatus("Select a project", "Open Projects and select a project before browsing outputs.", InfoBarSeverity.Informational);
            return;
        }

        SetBusy(true);
        try
        {
            JsonElement outputs = await _apiClient.GetOutputsAsync(ActiveProjectId);
            Items.Clear();
            foreach (StudioOutputItem item in StudioOutputCatalog.Project(outputs))
            {
                Items.Add(item);
            }

            ApplyFilters(selectionIdentity);
            SetStatus(
                "Outputs refreshed",
                $"{Items.Count} output record(s) loaded for project {ActiveProjectId}.",
                InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            SetStatus("Unable to load outputs", ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void OutputsList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        await CancelPreviewAsync(clearSurface: false);
        StudioOutputItem? selected = SelectedOutput;
        UpdateSelectionUi(selected);

        if (selected is null)
        {
            OutputPreview.ShowEmpty("Select an output to preview.");
            return;
        }

        if (!selected.SupportsMediaWorkflow)
        {
            OutputPreview.ShowUnsupported("This Unreal bundle is a workflow artifact, not previewable media.");
            return;
        }

        _session.SetSelectedArtifact(selected.Path);
        _previewCts = new CancellationTokenSource();
        try
        {
            await _projectMediaClient.StreamProjectMediaAsync<bool>(
                ActiveProjectId,
                selected.Path,
                async (file, cancellationToken) =>
                {
                    if (selected.IsVideo)
                    {
                        await OutputPreview.LoadVideoStreamAsync(file.Stream, file.ContentHeaders.ContentLength, cancellationToken);
                    }
                    else
                    {
                        await OutputPreview.LoadStreamAsync(
                            file.Stream,
                            file.ContentHeaders.ContentType?.MediaType,
                            cancellationToken);
                    }
                    return true;
                },
                _previewCts.Token);
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            OutputPreview.ShowError(ex.Message);
            SetStatus("Preview failed", ex.Message, InfoBarSeverity.Warning);
        }
    }

    private void UpdateSelectionUi(StudioOutputItem? selected)
    {
        bool mediaWorkflow = selected?.SupportsMediaWorkflow == true;
        bool bundleWorkflow = selected?.SupportsBundleWorkflow == true;

        SelectedNameText.Text = selected?.Name ?? "Select an output";
        SelectedPathText.Text = selected?.Path ?? string.Empty;
        MetadataText.Text = selected?.Metadata?.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) ?? string.Empty;
        UnrealBundlePanel.Visibility = bundleWorkflow ? Visibility.Visible : Visibility.Collapsed;

        SaveButton.IsEnabled = mediaWorkflow;
        RevealButton.IsEnabled = mediaWorkflow;
        ReviewButton.IsEnabled = mediaWorkflow;
        TimelineButton.IsEnabled = mediaWorkflow;
        RenderButton.IsEnabled = mediaWorkflow;
        BuildUnrealPlanButton.IsEnabled = bundleWorkflow;
        ImportUnrealReturnButton.IsEnabled = bundleWorkflow;
        RevealBundleButton.IsEnabled = bundleWorkflow;
        SaveManifestButton.IsEnabled = bundleWorkflow && !string.IsNullOrWhiteSpace(selected?.ManifestPath);
        SavePlanButton.IsEnabled = bundleWorkflow && !string.IsNullOrWhiteSpace(selected?.ImportPlanPath);
        SaveZipButton.IsEnabled = bundleWorkflow && !string.IsNullOrWhiteSpace(selected?.ZipPath);

        if (!mediaWorkflow)
        {
            _session.SetSelectedArtifact(null);
            _session.SetSourceAsset(null);
        }
    }

    private async Task CancelPreviewAsync(bool clearSurface)
    {
        CancellationTokenSource? cts = Interlocked.Exchange(ref _previewCts, null);
        if (cts is not null)
        {
            await cts.CancelAsync();
            cts.Dispose();
        }

        if (clearSurface)
        {
            OutputPreview.ShowEmpty();
        }

        DeletePreviewTemp();
    }

    private void DeletePreviewTemp()
    {
        string? tempPath = Interlocked.Exchange(ref _previewTempPath, null);
        if (string.IsNullOrWhiteSpace(tempPath))
        {
            return;
        }

        try
        {
            File.Delete(tempPath);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async void SaveButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.IsDownloadable != true)
        {
            return;
        }

        try
        {
            await SaveProjectArtifactAsync(selected.Path, selected.Name, "Output file");
        }
        catch (Exception ex)
        {
            SetStatus("Save failed", ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void RevealButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.IsDownloadable != true)
        {
            return;
        }

        SetBusy(true);
        try
        {
            DeletePreviewTemp();
            string extension = Path.GetExtension(selected.Name);
            _previewTempPath = Path.Combine(Path.GetTempPath(), $"{Guid.NewGuid():N}{extension}");
            await _projectMediaClient.StreamProjectMediaAsync<bool>(
                ActiveProjectId,
                selected.Path,
                async (file, cancellationToken) =>
                {
                    await using FileStream destination = File.Create(_previewTempPath);
                    await file.Stream.CopyToAsync(destination, cancellationToken);
                    return true;
                });

            using System.Diagnostics.Process? process = System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo
                {
                    FileName = "explorer.exe",
                    Arguments = $"/select,\"{_previewTempPath}\"",
                    UseShellExecute = true,
                });
        }
        catch (Exception ex)
        {
            SetStatus("Reveal failed", ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void ExportUnrealButton_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(ActiveProjectId))
        {
            SetStatus("Select a project", "Select a project before exporting an Unreal bundle.", InfoBarSeverity.Warning);
            return;
        }

        SetBusy(true);
        try
        {
            double displayedVariant = UnrealVariantNumber.Value;
            if (double.IsNaN(displayedVariant) ||
                displayedVariant < 1 ||
                displayedVariant > int.MaxValue ||
                displayedVariant != Math.Truncate(displayedVariant))
            {
                throw new InvalidOperationException("Plan variant must be a whole number of 1 or greater.");
            }

            UnrealBundleExportResponse response = await _apiClient.ExportUnrealBundleAsync(
                ActiveProjectId,
                new UnrealBundleExportRequest
                {
                    VariantIndex = checked((int)displayedVariant - 1),
                    BundleName = OptionalText(UnrealBundleNameBox.Text),
                    IncludeZip = UnrealIncludeZipCheckBox.IsChecked == true,
                });

            string stableIdentity = $"bundle:{StudioOutputCatalog.NormalizePath(response.Bundle.BundleDirectory)}";
            await RefreshAsync(stableIdentity);
            SetStatus(
                "Unreal bundle exported",
                $"Created {response.Bundle.BundleDirectory}.",
                InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            SetStatus("Unreal export failed", ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void BuildUnrealPlanButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.SupportsBundleWorkflow != true || string.IsNullOrWhiteSpace(selected.BundleDirectory))
        {
            return;
        }

        SetBusy(true);
        try
        {
            UnrealImportPlanResponse response = await _apiClient.BuildUnrealImportPlanAsync(
                ActiveProjectId,
                new UnrealImportPlanRequest
                {
                    BundleDirectory = selected.BundleDirectory,
                    ContentPath = OptionalText(UnrealContentPathBox.Text),
                    AssetName = OptionalText(UnrealAssetNameBox.Text),
                });

            await RefreshAsync(selected.StableIdentity);
            SetStatus("Unreal import plan created", $"Created {response.PlanPath}.", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            SetStatus("Import plan failed", ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void ImportUnrealReturnButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.SupportsBundleWorkflow != true || string.IsNullOrWhiteSpace(selected.BundleDirectory))
        {
            return;
        }

        SetBusy(true);
        try
        {
            UnrealReturnImportResponse response = await _apiClient.ImportUnrealReturnsAsync(
                ActiveProjectId,
                new UnrealReturnImportRequest
                {
                    BundleDirectory = selected.BundleDirectory,
                    SourceDirectory = OptionalText(UnrealReturnSourceBox.Text),
                });

            await RefreshAsync(selected.StableIdentity);
            SetStatus(
                "Unreal returns imported",
                $"{response.Imported.Media.Count} returned media file(s) added to Studio.",
                InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            SetStatus("Return import failed", ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void RevealBundleButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.SupportsBundleWorkflow != true || string.IsNullOrWhiteSpace(selected.BundleDirectory))
        {
            return;
        }

        try
        {
            ManagedProjectPathResolution resolution = ManagedProjectPathResolver.Resolve(
                _backendConfiguration.Mode,
                _backendConfiguration.Paths.DataDirectory,
                ActiveProjectId,
                selected.BundleDirectory);
            if (!resolution.IsAvailable || string.IsNullOrWhiteSpace(resolution.FullPath))
            {
                throw new InvalidOperationException(resolution.ErrorMessage);
            }

            if (!Directory.Exists(resolution.FullPath))
            {
                throw new DirectoryNotFoundException("The Unreal bundle directory does not exist locally.");
            }

            StorageFolder folder = await StorageFolder.GetFolderFromPathAsync(resolution.FullPath);
            if (!await Launcher.LaunchFolderAsync(folder))
            {
                throw new InvalidOperationException("Windows could not open the Unreal bundle folder.");
            }
        }
        catch (Exception ex)
        {
            SetStatus("Reveal bundle failed", ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void SaveManifestButton_Click(object sender, RoutedEventArgs e) =>
        await SaveSelectedBundleArtifactAsync(
            SelectedOutput?.ManifestPath,
            "unreal-manifest.json",
            "Unreal manifest",
            "Manifest save failed");

    private async void SavePlanButton_Click(object sender, RoutedEventArgs e) =>
        await SaveSelectedBundleArtifactAsync(
            SelectedOutput?.ImportPlanPath,
            "unreal-import-plan.json",
            "Unreal import plan",
            "Import plan save failed");

    private async void SaveZipButton_Click(object sender, RoutedEventArgs e) =>
        await SaveSelectedBundleArtifactAsync(
            SelectedOutput?.ZipPath,
            "unreal-bundle.zip",
            "Unreal bundle archive",
            "Bundle save failed");

    private async Task SaveSelectedBundleArtifactAsync(
        string? projectRelativePath,
        string fallbackName,
        string description,
        string errorTitle)
    {
        if (SelectedOutput?.SupportsBundleWorkflow != true || string.IsNullOrWhiteSpace(projectRelativePath))
        {
            return;
        }

        try
        {
            string suggestedName = Path.GetFileName(projectRelativePath.Replace('/', Path.DirectorySeparatorChar));
            await SaveProjectArtifactAsync(
                projectRelativePath,
                string.IsNullOrWhiteSpace(suggestedName) ? fallbackName : suggestedName,
                description);
        }
        catch (Exception ex)
        {
            SetStatus(errorTitle, ex.Message, InfoBarSeverity.Error);
        }
    }

    private async Task SaveProjectArtifactAsync(string projectRelativePath, string suggestedName, string description)
    {
        string extension = Path.GetExtension(suggestedName);
        if (string.IsNullOrWhiteSpace(extension))
        {
            extension = ".bin";
        }

        FileSavePicker picker = new()
        {
            SuggestedFileName = Path.GetFileNameWithoutExtension(suggestedName),
        };
        picker.FileTypeChoices.Add(description, [extension]);
        MainWindow mainWindow = App.MainWindowInstance ??
            throw new InvalidOperationException("The Studio window is not available.");
        InitializeWithWindow.Initialize(picker, mainWindow.WindowHandle);
        StorageFile? destination = await picker.PickSaveFileAsync();
        if (destination is null)
        {
            return;
        }

        await _projectMediaClient.StreamProjectMediaAsync<bool>(
            ActiveProjectId,
            projectRelativePath,
            async (file, cancellationToken) =>
            {
                await using Stream output = await destination.OpenStreamForWriteAsync();
                output.SetLength(0);
                await file.Stream.CopyToAsync(output, cancellationToken);
                await output.FlushAsync(cancellationToken);
                return true;
            });

        SetStatus("File saved", $"Saved {destination.Name}.", InfoBarSeverity.Success);
    }

    private void SearchBox_TextChanged(AutoSuggestBox sender, AutoSuggestBoxTextChangedEventArgs args)
    {
        if (args.Reason == AutoSuggestionBoxTextChangeReason.UserInput)
        {
            ApplyFilters();
        }
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e) => ApplyFilters();

    private void ApplyFilters(string? preferredStableIdentity = null)
    {
        string? selectionIdentity = preferredStableIdentity ?? SelectedOutput?.StableIdentity;
        string? kindFilter = KindFilter.SelectedIndex switch
        {
            1 => "IMAGES",
            2 => "VIDEOS",
            3 => "UNREAL",
            4 => "OTHER",
            _ => null,
        };
        StudioOutputSort sortOrder = SortOrder.SelectedIndex switch
        {
            1 => StudioOutputSort.Name,
            2 => StudioOutputSort.SizeDescending,
            _ => StudioOutputSort.Newest,
        };

        VisibleItems.Clear();
        foreach (StudioOutputItem item in StudioOutputCatalog.FilterAndSort(
                     Items,
                     SearchBox.Text,
                     kindFilter,
                     sortOrder))
        {
            VisibleItems.Add(item);
        }

        OutputsList.SelectedItem = VisibleItems.FirstOrDefault(
            item => string.Equals(item.StableIdentity, selectionIdentity, StringComparison.OrdinalIgnoreCase));
    }

    private void ReviewButton_Click(object sender, RoutedEventArgs e)
    {
        if (SelectedOutput?.SupportsMediaWorkflow != true)
        {
            return;
        }

        _session.SetLastWorkflowDestination("review");
        Frame.Navigate(typeof(ReviewPage));
    }

    private void TimelineButton_Click(object sender, RoutedEventArgs e)
    {
        if (SelectedOutput?.SupportsMediaWorkflow != true)
        {
            return;
        }

        _session.SetLastWorkflowDestination("timeline");
        Frame.Navigate(typeof(TimelinePage));
    }

    private void RenderButton_Click(object sender, RoutedEventArgs e)
    {
        StudioOutputItem? selected = SelectedOutput;
        if (selected?.SupportsMediaWorkflow != true)
        {
            return;
        }

        _session.SetSourceAsset(selected.Path);
        _session.SetLastWorkflowDestination("render");
        Frame.Navigate(typeof(RenderPage));
    }

    private void SetBusy(bool busy)
    {
        BusyRing.IsActive = busy;
        RefreshButton.IsEnabled = !busy;
        ExportUnrealButton.IsEnabled = !busy;
        OutputsList.IsEnabled = !busy;
        if (busy)
        {
            SaveButton.IsEnabled = false;
            RevealButton.IsEnabled = false;
            ReviewButton.IsEnabled = false;
            TimelineButton.IsEnabled = false;
            RenderButton.IsEnabled = false;
            BuildUnrealPlanButton.IsEnabled = false;
            ImportUnrealReturnButton.IsEnabled = false;
            RevealBundleButton.IsEnabled = false;
            SaveManifestButton.IsEnabled = false;
            SavePlanButton.IsEnabled = false;
            SaveZipButton.IsEnabled = false;
        }
        else
        {
            UpdateSelectionUi(SelectedOutput);
        }
    }

    private void SetStatus(string title, string message, InfoBarSeverity severity)
    {
        StatusInfoBar.Title = title;
        StatusInfoBar.Message = message;
        StatusInfoBar.Severity = severity;
        StatusInfoBar.IsOpen = true;
    }

    private static string? OptionalText(string? value) =>
        string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}
