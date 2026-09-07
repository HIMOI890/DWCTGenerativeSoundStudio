using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.UI;

namespace EdmgStudio.WinUI.Pages;

public sealed partial class TimelinePage : Page
{
    private const double DefaultDurationSeconds = 60;
    private const double DefaultFps = 30;
    private const double TrackHeight = 56;
    private const double ClipVerticalInset = 6;
    private const double MinimumPixelsPerSecond = 12;
    private const double MaximumPixelsPerSecond = 360;
    private const int HistoryLimit = 50;
    private const string PointerToolSettingKey = "Timeline.PointerTool";

    private readonly DispatcherTimer _transportTimer = new()
    {
        Interval = TimeSpan.FromMilliseconds(1000 / DefaultFps)
    };
    private readonly Stopwatch _transportWatch = new();
    private readonly List<JsonObject> _undoHistory = [];
    private readonly List<JsonObject> _redoHistory = [];
    private readonly ObservableCollection<CameraKeyframeListItem> _cameraKeyframeItems = [];
    private readonly ApplicationDataContainer? _settings;

    private CancellationTokenSource? _pageCancellation;
    private CancellationTokenSource? _previewCancellation;
    private CancellationTokenSource? _automationCancellation;
    private JsonObject? _timelineDocument;
    private JsonObject? _recoveryDocument;
    private IReadOnlyList<TimelineLaneDocument> _lanes = [];
    private IReadOnlyList<TimelineCameraKeyframeDocument> _cameraKeyframes = [];
    private ProjectDto? _project;
    private string? _loadedProjectId;
    private string? _selectedLaneId;
    private string? _selectedCameraKeyframeIdentity;
    private string _selectedSourcePath = string.Empty;
    private int _loadedVariantIndex;
    private Border? _selectedClipBorder;
    private Line? _playheadLine;
    private TimelineLaneDocument? _dragOriginalLane;
    private TimelineLaneDocument? _dragProvisionalLane;
    private JsonObject? _dragBeforeSnapshot;
    private Border? _dragBorder;
    private uint _dragPointerId;
    private Point _dragStartPoint;
    private DragMode _dragMode;
    private double _durationSeconds = DefaultDurationSeconds;
    private double _positionSeconds;
    private double _transportAnchorSeconds;
    private double _pixelsPerSecond = 80;
    private long _previewGeneration;
    private bool _isLoaded;
    private bool _isBusy;
    private bool _isAutomationBusy;
    private bool _isDirty;
    private bool _isPlaying;
    private bool _rippleEnabled;
    private bool _positionPointerActive;
    private bool _updatingPosition;
    private bool _updatingPointerTool;
    private bool _syncingScroll;
    private bool _suppressSessionChange;
    private bool _suppressCameraSelectionChange;
    private bool _revisionConflictInterruptedOperation;
    private TimelinePointerTool _pointerTool;

    public TimelinePage()
    {
        InitializeComponent();
        try
        {
            _settings = ApplicationData.Current.LocalSettings;
        }
        catch
        {
            _settings = null;
        }

        _pointerTool = LoadPointerTool();
        CameraKeyframeListView.ItemsSource = _cameraKeyframeItems;
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
        _transportTimer.Tick += TransportTimer_Tick;
        ApplyPointerToolUi();
        UpdateTransportUi();
        UpdateCommandState();
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_isLoaded)
        {
            return;
        }

        _isLoaded = true;
        App.Services.Session.Changed += Session_Changed;
        await LoadActiveProjectAsync();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        if (!_isLoaded)
        {
            return;
        }

        _isLoaded = false;
        App.Services.Session.Changed -= Session_Changed;
        StopPlayback();
        CancelPreview();
        _automationCancellation?.Cancel();
        _automationCancellation?.Dispose();
        _automationCancellation = null;
        _pageCancellation?.Cancel();
        _pageCancellation?.Dispose();
        _pageCancellation = null;
    }

    private void Session_Changed(object? sender, EventArgs e)
    {
        if (!_isLoaded || _suppressSessionChange)
        {
            return;
        }

        DispatcherQueue.TryEnqueue(HandleSessionChangeAsync);
    }

    private async void HandleSessionChangeAsync()
    {
        string requestedProjectId = App.Services.Session.ActiveProjectId;
        if (string.Equals(requestedProjectId, _loadedProjectId, StringComparison.Ordinal))
        {
            _loadedVariantIndex = App.Services.Session.SelectedVariantIndex;
            RefreshWorkflowPlanSummary();
            UpdateCommandState();
            return;
        }

        if (_isDirty && !string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            bool replace = await ConfirmAsync(
                "Switch Timeline project?",
                "The active Workspace project changed. Switch projects and discard the current unsaved Timeline edits?",
                "Switch project");
            if (!replace)
            {
                _suppressSessionChange = true;
                try
                {
                    App.Services.Session.ActiveProjectId = _loadedProjectId;
                    App.Services.Session.SelectedVariantIndex = _loadedVariantIndex;
                }
                finally
                {
                    _suppressSessionChange = false;
                }

                ShowAutomationInfo(
                    "Project switch canceled. Your Timeline edits are still loaded.",
                    InfoBarSeverity.Informational);
                RefreshWorkflowPlanSummary();
                UpdateCommandState();
                return;
            }
        }

        await LoadActiveProjectAsync();
    }

    private async Task LoadActiveProjectAsync(bool forceReload = false)
    {
        string? projectId = App.Services.Session.ActiveProjectId;
        if (string.IsNullOrWhiteSpace(projectId))
        {
            ClearTimeline("Select a project in Projects to begin editing.");
            return;
        }

        if (!forceReload &&
            _isBusy &&
            string.Equals(_loadedProjectId, projectId, StringComparison.Ordinal))
        {
            return;
        }

        _pageCancellation?.Cancel();
        _pageCancellation?.Dispose();
        _pageCancellation = new CancellationTokenSource();
        CancellationToken cancellationToken = _pageCancellation.Token;

        SetBusy(true);
        StopPlayback();
        CancelPreview();
        ResetAiEditProposal();
        SourceAssetComboBox.ItemsSource = null;
        SourceAssetComboBox.SelectedItem = null;
        SelectSource(string.Empty);
        PageInfoBar.IsOpen = false;
        StatusText.Text = "Loading timeline...";

        try
        {
            var projectTask = App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
            var timelineTask = App.Services.ApiClient.GetTimelineAsync(projectId, cancellationToken);
            var recoveryTask = App.Services.ApiClient.GetRecoveryAsync(projectId, cancellationToken);
            await Task.WhenAll(projectTask, timelineTask, recoveryTask);

            _project = projectTask.Result.Project;
            _loadedProjectId = projectId;
            _loadedVariantIndex = App.Services.Session.SelectedVariantIndex;
            _timelineDocument = ExtractTimeline(timelineTask.Result);
            _recoveryDocument = JsonNode.Parse(recoveryTask.Result.GetRawText()) as JsonObject;
            _lanes = TimelineProjection.Project(_timelineDocument);
            _cameraKeyframes = TimelineCameraProjection.Project(_timelineDocument);
            _durationSeconds = ResolveDuration(_project, _lanes);
            _positionSeconds = 0;
            _selectedLaneId = null;
            _selectedCameraKeyframeIdentity = null;
            _undoHistory.Clear();
            _redoHistory.Clear();
            _isDirty = false;

            RefreshEditor(updateRawText: true);
            RefreshRecoverySummary();
            RefreshWorkflowPlanSummary();
            try
            {
                await LoadWorkflowAssetsAsync(cancellationToken);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                ShowAutomationInfo(
                    $"Timeline loaded, but project sources could not be refreshed: {ex.Message}",
                    InfoBarSeverity.Warning);
            }

            StatusText.Text = "Timeline ready.";
            await RefreshPreviewAsync(force: false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            ClearTimeline("The timeline could not be loaded.");
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private static JsonObject ExtractTimeline(JsonElement response)
    {
        JsonElement timeline = response;
        if (response.ValueKind == JsonValueKind.Object &&
            response.TryGetProperty("timeline", out JsonElement wrappedTimeline))
        {
            timeline = wrappedTimeline;
        }

        return JsonNode.Parse(timeline.GetRawText()) as JsonObject
            ?? throw new JsonException("The backend returned an invalid timeline document.");
    }

    private static double ResolveDuration(
        ProjectDto? project,
        IReadOnlyList<TimelineLaneDocument> lanes)
    {
        if (project?.DurationSeconds is double projectDuration &&
            double.IsFinite(projectDuration) &&
            projectDuration > 0)
        {
            return projectDuration;
        }

        double laneDuration = lanes.Count == 0 ? 0 : lanes.Max(lane => lane.EndSeconds);
        return laneDuration > 0 ? laneDuration : DefaultDurationSeconds;
    }

    private void ClearTimeline(string message)
    {
        StopPlayback();
        CancelPreview();
        _loadedProjectId = null;
        _project = null;
        _timelineDocument = null;
        _recoveryDocument = null;
        _lanes = [];
        _cameraKeyframes = [];
        _selectedLaneId = null;
        _selectedCameraKeyframeIdentity = null;
        _selectedSourcePath = string.Empty;
        _undoHistory.Clear();
        _redoHistory.Clear();
        _isDirty = false;
        _positionSeconds = 0;
        ResetAiEditProposal();
        ProjectText.Text = "No active project";
        DurationSummaryText.Text = message;
        TimelineTextBox.Text = string.Empty;
        BackupSummaryText.Text = "No recovery information is available.";
        StatusText.Text = message;
        TrackHeadersPanel.Children.Clear();
        RulerCanvas.Children.Clear();
        TimelineCanvas.Children.Clear();
        _cameraKeyframeItems.Clear();
        CameraKeyframeListView.SelectedItem = null;
        SelectedClipTitle.Text = "No clip selected";
        SelectedClipSubtitle.Text = "Select a clip to inspect its timing and media properties.";
        PreviewSurface.ShowEmpty(message);
        PreviewHintText.Text = message;
        SourceAssetComboBox.ItemsSource = null;
        SourceAssetComboBox.SelectedItem = null;
        SelectedSourceText.Text = "No source selected.";
        RefreshCameraEditor();
        RefreshWorkflowPlanSummary();
        UpdateCommandState();
    }

    private void RefreshEditor(bool updateRawText)
    {
        if (_timelineDocument is null)
        {
            return;
        }

        _lanes = TimelineProjection.Project(_timelineDocument);
        _cameraKeyframes = TimelineCameraProjection.Project(_timelineDocument);
        _durationSeconds = Math.Max(
            TimelineProjection.MinimumDurationSeconds,
            ResolveDuration(_project, _lanes));
        _positionSeconds = Math.Clamp(_positionSeconds, 0, _durationSeconds);

        if (_selectedLaneId is not null &&
            !_lanes.Any(lane => lane.StableId == _selectedLaneId))
        {
            _selectedLaneId = null;
        }
        if (_selectedCameraKeyframeIdentity is not null &&
            !_cameraKeyframes.Any(
                keyframe => keyframe.StableId == _selectedCameraKeyframeIdentity))
        {
            _selectedCameraKeyframeIdentity = null;
        }

        ProjectText.Text = _project?.Name ?? _loadedProjectId ?? "Timeline";
        int clipCount = _lanes.Count(lane => !lane.IsLayer);
        int overlayCount = _lanes.Count(lane => lane.IsLayer);
        DurationSummaryText.Text =
            $"{FormatClock(_durationSeconds)}  •  {clipCount} clips  •  {overlayCount} overlays  •  {TrackCount} tracks";
        PositionSlider.Maximum = _durationSeconds;
        CameraTimeNumberBox.Maximum = _durationSeconds;
        LoopInNumberBox.Maximum = _durationSeconds;
        LoopOutNumberBox.Maximum = _durationSeconds;
        if (!double.IsFinite(LoopOutNumberBox.Value) ||
            LoopOutNumberBox.Value <= 0 ||
            LoopOutNumberBox.Value > _durationSeconds)
        {
            LoopOutNumberBox.Value = _durationSeconds;
        }

        if (updateRawText)
        {
            TimelineTextBox.Text = _timelineDocument.ToJsonString(new JsonSerializerOptions
            {
                WriteIndented = true
            });
        }

        RenderTrackHeaders();
        RenderRuler();
        RenderTimeline();
        PopulateInspector();
        RefreshCameraEditor();
        UpdateTransportUi();
        UpdateCommandState();
    }

    private int TrackCount =>
        Math.Max(
            1,
            _lanes
                .Where(lane => !lane.IsLayer)
                .Select(lane => lane.TrackIndex + 1)
                .DefaultIfEmpty(1)
                .Max());

    private int OverlayVisualTrackIndex => TrackCount;

    private int CameraVisualTrackIndex => TrackCount + 1;

    private int VisualTrackCount => TrackCount + 2;

    private double SurfaceWidth =>
        Math.Max(720, Math.Ceiling(_durationSeconds * _pixelsPerSecond));

    private void RenderTrackHeaders()
    {
        TrackHeadersPanel.Children.Clear();
        JsonObject? timelineDocument = _timelineDocument;
        for (int trackIndex = 0; trackIndex < TrackCount; trackIndex++)
        {
            var panel = new Grid
            {
                Height = TrackHeight,
                Padding = new Thickness(12, 7, 10, 6),
                BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                BorderThickness = new Thickness(0, 0, 0, 1)
            };
            panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            panel.ColumnDefinitions.Add(new ColumnDefinition());
            panel.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var title = new TextBlock
            {
                Text = $"Track {trackIndex + 1}",
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                TextTrimming = TextTrimming.CharacterEllipsis
            };
            var detail = new TextBlock
            {
                Text = $"{_lanes.Count(lane => !lane.IsLayer && lane.TrackIndex == trackIndex)} clips",
                Opacity = 0.62,
                FontSize = 11
            };
            Grid.SetRow(detail, 1);
            var lockButton = new Button
            {
                Tag = trackIndex,
                Content = timelineDocument is not null &&
                          TimelineProjection.IsTrackLocked(timelineDocument, trackIndex)
                    ? "Unlock"
                    : "Lock",
                Padding = new Thickness(7, 2, 7, 2),
                VerticalAlignment = VerticalAlignment.Center
            };
            Grid.SetColumn(lockButton, 1);
            Grid.SetRowSpan(lockButton, 2);
            lockButton.Click += TrackLockButton_Click;
            panel.Children.Add(title);
            panel.Children.Add(detail);
            panel.Children.Add(lockButton);
            TrackHeadersPanel.Children.Add(panel);
        }

        var overlayPanel = new Grid
        {
            Height = TrackHeight,
            Padding = new Thickness(12, 7, 10, 6),
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(0, 0, 0, 1)
        };
        overlayPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        overlayPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var overlayTitle = new TextBlock
        {
            Text = "Overlays",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        var overlayDetail = new TextBlock
        {
            Text = $"{_lanes.Count(lane => lane.IsLayer)} layers",
            Opacity = 0.62,
            FontSize = 11
        };
        Grid.SetRow(overlayDetail, 1);
        overlayPanel.Children.Add(overlayTitle);
        overlayPanel.Children.Add(overlayDetail);
        TrackHeadersPanel.Children.Add(overlayPanel);

        var cameraPanel = new Grid
        {
            Height = TrackHeight,
            Padding = new Thickness(12, 7, 10, 6),
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(0, 0, 0, 1)
        };
        cameraPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        cameraPanel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var cameraTitle = new TextBlock
        {
            Text = "Camera",
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        var cameraDetail = new TextBlock
        {
            Text = $"{_cameraKeyframes.Count} keyframes",
            Opacity = 0.62,
            FontSize = 11
        };
        Grid.SetRow(cameraDetail, 1);
        cameraPanel.Children.Add(cameraTitle);
        cameraPanel.Children.Add(cameraDetail);
        TrackHeadersPanel.Children.Add(cameraPanel);
    }

    private void RenderRuler()
    {
        RulerCanvas.Children.Clear();
        RulerCanvas.Width = SurfaceWidth;
        double labelStep = ResolveRulerStep();
        int tickCount = (int)Math.Ceiling(_durationSeconds / labelStep);
        for (int index = 0; index <= tickCount; index++)
        {
            double seconds = Math.Min(_durationSeconds, index * labelStep);
            double x = seconds * _pixelsPerSecond;
            var line = new Line
            {
                X1 = x,
                X2 = x,
                Y1 = 27,
                Y2 = 36,
                Stroke = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
                StrokeThickness = 1
            };
            var label = new TextBlock
            {
                Text = FormatRulerTime(seconds),
                FontSize = 10,
                Opacity = 0.68
            };
            Canvas.SetLeft(label, x + 4);
            Canvas.SetTop(label, 5);
            RulerCanvas.Children.Add(line);
            RulerCanvas.Children.Add(label);
        }
    }

    private double ResolveRulerStep()
    {
        ReadOnlySpan<double> candidates = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
        foreach (double candidate in candidates)
        {
            if (candidate * _pixelsPerSecond >= 76)
            {
                return candidate;
            }
        }

        return 600;
    }

    private void RenderTimeline()
    {
        TimelineCanvas.Children.Clear();
        TimelineCanvas.Width = SurfaceWidth;
        TimelineCanvas.Height = VisualTrackCount * TrackHeight;
        _selectedClipBorder = null;

        for (int trackIndex = 0; trackIndex < VisualTrackCount; trackIndex++)
        {
            var separator = new Line
            {
                X1 = 0,
                X2 = SurfaceWidth,
                Y1 = (trackIndex + 1) * TrackHeight,
                Y2 = (trackIndex + 1) * TrackHeight,
                Stroke = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
                StrokeThickness = 1
            };
            TimelineCanvas.Children.Add(separator);
        }

        if (_lanes.Count == 0 && _cameraKeyframes.Count == 0)
        {
            var empty = new TextBlock
            {
                Text = "This timeline has no clips, overlays, or camera keyframes. Use the inspector commands to add one at the playhead.",
                Opacity = 0.65,
                FontSize = 13
            };
            Canvas.SetLeft(empty, 24);
            Canvas.SetTop(empty, 20);
            TimelineCanvas.Children.Add(empty);
        }

        foreach (TimelineLaneDocument lane in TimelineProjection.OrderLanes(_lanes))
        {
            Border border = CreateClipVisual(lane);
            TimelineCanvas.Children.Add(border);
            if (lane.StableId == _selectedLaneId)
            {
                _selectedClipBorder = border;
            }
        }

        foreach (TimelineCameraKeyframeDocument keyframe in _cameraKeyframes)
        {
            TimelineCanvas.Children.Add(CreateCameraKeyframeVisual(keyframe));
        }

        _playheadLine = new Line
        {
            X1 = _positionSeconds * _pixelsPerSecond,
            X2 = _positionSeconds * _pixelsPerSecond,
            Y1 = 0,
            Y2 = TimelineCanvas.Height,
            Stroke = new SolidColorBrush(Colors.White),
            StrokeThickness = 2,
            IsHitTestVisible = false
        };
        TimelineCanvas.Children.Add(_playheadLine);
    }

    private Button CreateCameraKeyframeVisual(TimelineCameraKeyframeDocument keyframe)
    {
        bool isSelected = keyframe.StableId == _selectedCameraKeyframeIdentity;
        var marker = new Button
        {
            Tag = keyframe.StableId,
            Content = "\u25C6",
            Width = 28,
            Height = TrackHeight - 12,
            Padding = new Thickness(0),
            Opacity = isSelected ? 1 : 0.78,
            BorderThickness = new Thickness(isSelected ? 3 : 1),
            HorizontalContentAlignment = HorizontalAlignment.Center,
            VerticalContentAlignment = VerticalAlignment.Center
        };
        AutomationProperties.SetAutomationId(marker, $"Timeline.CameraKeyframe.{keyframe.StableId}");
        AutomationProperties.SetName(marker, $"Camera keyframe at {FormatClock(keyframe.TimeSeconds)}");
        ToolTipService.SetToolTip(marker, $"Camera keyframe · {FormatClock(keyframe.TimeSeconds)}");
        Canvas.SetLeft(
            marker,
            Math.Clamp(
                (keyframe.TimeSeconds * _pixelsPerSecond) - (marker.Width / 2),
                0,
                Math.Max(0, SurfaceWidth - marker.Width)));
        Canvas.SetTop(marker, (CameraVisualTrackIndex * TrackHeight) + 6);
        marker.Click += CameraKeyframeMarker_Click;
        return marker;
    }

    private Border CreateClipVisual(TimelineLaneDocument lane)
    {
        bool isSelected = lane.StableId == _selectedLaneId;
        var border = new Border
        {
            Tag = lane.StableId,
            Width = Math.Max(8, (lane.EndSeconds - lane.StartSeconds) * _pixelsPerSecond),
            Height = TrackHeight - (ClipVerticalInset * 2),
            Background = ResolveClipBrush(lane.Type, isSelected),
            BorderBrush = isSelected
                ? new SolidColorBrush(Colors.White)
                : new SolidColorBrush(Color.FromArgb(150, 255, 255, 255)),
            BorderThickness = new Thickness(isSelected ? 2 : 1),
            CornerRadius = new CornerRadius(5),
            Padding = new Thickness(8, 4, 8, 4)
        };
        AutomationProperties.SetAutomationId(border, $"Timeline.Lane.{lane.StableId}");
        AutomationProperties.SetName(
            border,
            $"{(lane.IsLayer ? "Overlay" : "Clip")} {lane.Name}, {FormatClock(lane.StartSeconds)} to {FormatClock(lane.EndSeconds)}");
        border.Child = new StackPanel
        {
            Spacing = 1,
            Children =
            {
                new TextBlock
                {
                    Text = lane.Name,
                    FontSize = 12,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
                    TextTrimming = TextTrimming.CharacterEllipsis
                },
                new TextBlock
                {
                    Text = $"{FormatClock(lane.StartSeconds)} – {FormatClock(lane.EndSeconds)}",
                    FontSize = 10,
                    Opacity = 0.72,
                    TextTrimming = TextTrimming.CharacterEllipsis
                }
            }
        };
        Canvas.SetLeft(border, lane.StartSeconds * _pixelsPerSecond);
        Canvas.SetTop(
            border,
            ((lane.IsLayer ? OverlayVisualTrackIndex : lane.TrackIndex) * TrackHeight) + ClipVerticalInset);
        border.PointerPressed += Clip_PointerPressed;
        border.PointerMoved += Clip_PointerMoved;
        border.PointerReleased += Clip_PointerReleased;
        border.PointerCanceled += Clip_PointerCanceled;
        return border;
    }

    private static Brush ResolveClipBrush(string type, bool selected)
    {
        Color color = type.Contains("audio", StringComparison.OrdinalIgnoreCase)
            ? Color.FromArgb(255, 21, 128, 111)
            : type.Contains("image", StringComparison.OrdinalIgnoreCase)
                ? Color.FromArgb(255, 117, 76, 153)
                : Color.FromArgb(255, 22, 100, 166);
        if (selected)
        {
            color = Color.FromArgb(
                color.A,
                (byte)Math.Min(255, color.R + 25),
                (byte)Math.Min(255, color.G + 25),
                (byte)Math.Min(255, color.B + 25));
        }

        return new SolidColorBrush(color);
    }

    private void SelectLane(string? stableId)
    {
        _selectedLaneId = stableId;
        _selectedCameraKeyframeIdentity = null;
        if (stableId is not null)
        {
            InspectorPivot.SelectedIndex = 0;
        }

        RenderTimeline();
        PopulateInspector();
        RefreshCameraEditor();
        UpdateCommandState();
    }

    private void SelectCameraKeyframe(string? stableId)
    {
        _selectedCameraKeyframeIdentity = stableId;
        _selectedLaneId = null;
        if (stableId is not null)
        {
            InspectorPivot.SelectedIndex = 4;
        }

        RenderTimeline();
        PopulateInspector();
        RefreshCameraEditor();
        UpdateCommandState();
    }

    private void CameraKeyframeMarker_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string stableId })
        {
            SelectCameraKeyframe(stableId);
        }
    }

    private TimelineLaneDocument? SelectedLane =>
        _selectedLaneId is null
            ? null
            : _lanes.FirstOrDefault(lane => lane.StableId == _selectedLaneId);

    private TimelineCameraKeyframeDocument? SelectedCameraKeyframe =>
        _selectedCameraKeyframeIdentity is null
            ? null
            : _cameraKeyframes.FirstOrDefault(
                keyframe => keyframe.StableId == _selectedCameraKeyframeIdentity);

    private TimelinePointerTool LoadPointerTool()
    {
        string? persisted = _settings?.Values[PointerToolSettingKey] as string;
        return string.Equals(persisted, "blade", StringComparison.OrdinalIgnoreCase)
            ? TimelinePointerTool.Blade
            : TimelinePointerTool.Select;
    }

    private void PersistPointerTool()
    {
        if (_settings is null)
        {
            return;
        }

        _settings.Values[PointerToolSettingKey] = _pointerTool == TimelinePointerTool.Blade
            ? "blade"
            : "select";
    }

    private void ApplyPointerToolUi()
    {
        if (_updatingPointerTool)
        {
            return;
        }

        _updatingPointerTool = true;
        try
        {
            SelectToolButton.IsChecked = _pointerTool == TimelinePointerTool.Select;
            BladeToolButton.IsChecked = _pointerTool == TimelinePointerTool.Blade;
        }
        finally
        {
            _updatingPointerTool = false;
        }
    }

    private void SetPointerTool(TimelinePointerTool tool)
    {
        _pointerTool = tool;
        ApplyPointerToolUi();
        PersistPointerTool();
    }

    private void PopulateInspector()
    {
        TimelineLaneDocument? lane = SelectedLane;
        bool enabled = lane is not null;
        bool videoAdjustmentsEnabled = lane is not null && IsVisualLane(lane);
        SelectedClipTitle.Text = lane?.Name ?? "No lane selected";
        SelectedClipSubtitle.Text = lane is null
            ? "Select a clip or overlay to inspect its timing and media properties."
            : lane.IsLayer
                ? $"{lane.Type} overlay • Layers"
                : $"{lane.Type} clip • Track {lane.TrackIndex + 1}";

        LaneNameTextBox.IsEnabled = enabled;
        LaneTypeTextBox.IsEnabled = enabled;
        StartNumberBox.IsEnabled = enabled;
        EndNumberBox.IsEnabled = enabled;
        SourcePathTextBox.IsEnabled = enabled;
        SourceInNumberBox.IsEnabled = enabled;
        SourceOutNumberBox.IsEnabled = enabled;
        SpeedNumberBox.IsEnabled = enabled;
        TrackNumberBox.IsEnabled = enabled && lane is not null && !lane.IsLayer;
        VolumeNumberBox.IsEnabled = enabled;
        MutedToggle.IsEnabled = enabled;
        FadeInNumberBox.IsEnabled = enabled;
        FadeOutNumberBox.IsEnabled = enabled;
        VideoAdjustmentsExpander.IsEnabled = videoAdjustmentsEnabled;

        LaneNameTextBox.Text = lane?.Name ?? string.Empty;
        LaneTypeTextBox.Text = lane?.Type ?? string.Empty;
        StartNumberBox.Value = lane?.StartSeconds ?? double.NaN;
        EndNumberBox.Value = lane?.EndSeconds ?? double.NaN;
        SourcePathTextBox.Text = lane?.SourcePath ?? string.Empty;
        SourceInNumberBox.Value = lane?.SourceInSeconds ?? double.NaN;
        SourceOutNumberBox.Value = lane?.SourceOutSeconds ?? double.NaN;
        SpeedNumberBox.Value = lane?.Speed ?? double.NaN;
        TrackNumberBox.Value = lane is null || lane.IsLayer
            ? double.NaN
            : lane.TrackIndex + 1;
        VolumeNumberBox.Value = lane?.Volume ?? double.NaN;
        MutedToggle.IsOn = lane?.Muted ?? false;
        FadeInNumberBox.Value = lane?.FadeInSeconds ?? double.NaN;
        FadeOutNumberBox.Value = lane?.FadeOutSeconds ?? double.NaN;
        SelectComboByTag(FitModeComboBox, lane?.FitMode ?? "contain");
        SelectComboByTag(RotationComboBox, (lane?.RotationDegrees ?? 0).ToString(CultureInfo.InvariantCulture));
        OpacityNumberBox.Value = lane?.Opacity ?? 1;
        BrightnessNumberBox.Value = lane?.Brightness ?? 0;
        ContrastNumberBox.Value = lane?.Contrast ?? 1;
        SaturationNumberBox.Value = lane?.Saturation ?? 1;
        FlipHorizontalToggle.IsOn = lane?.FlipHorizontal ?? false;
        SelectComboByTag(VideoLookComboBox, "neutral");
        VideoAdjustmentHintText.Text = videoAdjustmentsEnabled
            ? "These nondestructive adjustments are saved with the clip and applied to the edited master."
            : "Video adjustments are available for video clips and source-backed visual overlays.";
        TrackInspectorHintText.Text = lane?.IsLayer == true
            ? "Overlays remain in the Layers row; timing and media edits are still available."
            : "Track clips can move between numbered tracks.";
        ApplyInspectorButton.Content = lane?.IsLayer == true
            ? "Apply overlay changes"
            : "Apply clip changes";
        DeleteClipButton.Content = lane?.IsLayer == true
            ? "Delete selected overlay"
            : "Delete selected clip";
    }

    private void RefreshCameraEditor()
    {
        _suppressCameraSelectionChange = true;
        try
        {
            _cameraKeyframeItems.Clear();
            for (int index = 0; index < _cameraKeyframes.Count; index++)
            {
                TimelineCameraKeyframeDocument cameraKeyframe = _cameraKeyframes[index];
                _cameraKeyframeItems.Add(new CameraKeyframeListItem
                {
                    StableId = cameraKeyframe.StableId,
                    Summary = $"Keyframe {index + 1}",
                    Detail = BuildCameraKeyframeDetail(cameraKeyframe)
                });
            }

            CameraKeyframeListView.SelectedItem = _cameraKeyframeItems.FirstOrDefault(
                item => item.StableId == _selectedCameraKeyframeIdentity);

            TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
            bool hasSelection = keyframe is not null;
            SetCameraEditorEnabled(hasSelection);
            CameraSelectionHintText.Text = hasSelection
                ? $"Editing {FormatClock(keyframe!.TimeSeconds)}"
                : _cameraKeyframes.Count == 0
                    ? "No camera keyframes. Add one at the playhead."
                    : "Select a keyframe to edit camera values.";
            if (keyframe is null)
            {
                CameraTimeNumberBox.Value = double.NaN;
                CameraTranslationXNumberBox.Value = double.NaN;
                CameraTranslationYNumberBox.Value = double.NaN;
                CameraTranslationZNumberBox.Value = double.NaN;
                CameraRotationXNumberBox.Value = double.NaN;
                CameraRotationYNumberBox.Value = double.NaN;
                CameraRotationZNumberBox.Value = double.NaN;
                CameraZoomNumberBox.Value = double.NaN;
                CameraFovNumberBox.Value = double.NaN;
                return;
            }

            CameraTimeNumberBox.Value = keyframe.TimeSeconds;
            CameraTranslationXNumberBox.Value = keyframe.TranslationX ?? double.NaN;
            CameraTranslationYNumberBox.Value = keyframe.TranslationY ?? double.NaN;
            CameraTranslationZNumberBox.Value = keyframe.TranslationZ ?? double.NaN;
            CameraRotationXNumberBox.Value = keyframe.RotationX ?? double.NaN;
            CameraRotationYNumberBox.Value = keyframe.RotationY ?? double.NaN;
            CameraRotationZNumberBox.Value = keyframe.RotationZ ?? double.NaN;
            CameraZoomNumberBox.Value = keyframe.Zoom ?? double.NaN;
            CameraFovNumberBox.Value = keyframe.Fov ?? double.NaN;
        }
        finally
        {
            _suppressCameraSelectionChange = false;
        }
    }

    private static string BuildCameraKeyframeDetail(TimelineCameraKeyframeDocument keyframe)
    {
        var fields = new List<string> { FormatClock(keyframe.TimeSeconds) };
        if (keyframe.Zoom is { } zoom)
        {
            fields.Add($"zoom {zoom:0.###}");
        }

        if (keyframe.Fov is { } fov)
        {
            fields.Add($"fov {fov:0.###}");
        }

        return string.Join("  •  ", fields);
    }

    private void SetCameraEditorEnabled(bool isEnabled)
    {
        CameraTimeNumberBox.IsEnabled = isEnabled;
        CameraTranslationXNumberBox.IsEnabled = isEnabled;
        CameraTranslationYNumberBox.IsEnabled = isEnabled;
        CameraTranslationZNumberBox.IsEnabled = isEnabled;
        CameraRotationXNumberBox.IsEnabled = isEnabled;
        CameraRotationYNumberBox.IsEnabled = isEnabled;
        CameraRotationZNumberBox.IsEnabled = isEnabled;
        CameraZoomNumberBox.IsEnabled = isEnabled;
        CameraFovNumberBox.IsEnabled = isEnabled;
        ApplyCameraButton.IsEnabled = isEnabled;
        MoveCameraButton.IsEnabled = isEnabled;
        QuantizeCameraButton.IsEnabled = isEnabled && CanQuantizeToCurrentGrid();
        DuplicateCameraButton.IsEnabled = isEnabled;
        DeleteCameraButton.IsEnabled = isEnabled;
    }

    private void CameraKeyframeListView_SelectionChanged(
        object sender,
        SelectionChangedEventArgs e)
    {
        if (_suppressCameraSelectionChange)
        {
            return;
        }

        SelectCameraKeyframe(
            (CameraKeyframeListView.SelectedItem as CameraKeyframeListItem)?.StableId);
    }

    private async void AddCameraKeyframe_Click(object sender, RoutedEventArgs e) =>
        await AddCameraKeyframeAtPlayheadAsync();

    private async void ApplyCameraKeyframe_Click(object sender, RoutedEventArgs e) =>
        await ApplyCameraKeyframeEditorAsync();

    private async void MoveCameraToPlayhead_Click(object sender, RoutedEventArgs e) =>
        await MoveSelectedCameraToPlayheadAsync();

    private async void QuantizeCamera_Click(object sender, RoutedEventArgs e) =>
        await QuantizeSelectedCameraKeyframeAsync();

    private async void DuplicateCameraKeyframe_Click(object sender, RoutedEventArgs e) =>
        await DuplicateSelectedCameraKeyframeAsync();

    private async void DeleteCameraKeyframe_Click(object sender, RoutedEventArgs e) =>
        await DeleteSelectedCameraKeyframeAsync();

    private async Task AddCameraKeyframeAtPlayheadAsync()
    {
        if (_timelineDocument is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        double? durationSeconds = TimelineCameraProjection.GetDurationSeconds(_timelineDocument);
        TimelineCameraKeyframeDocument created = TimelineCameraProjection.CreateAt(
            SnapTime(_positionSeconds),
            durationSeconds);
        var updated = _cameraKeyframes.ToList();
        updated.Add(created);
        await CommitCameraKeyframesAsync(
            before,
            updated,
            "camera keyframe added",
            created.StableId);
    }

    private async Task ApplyCameraKeyframeEditorAsync()
    {
        TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
        if (_timelineDocument is null || keyframe is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        double? durationSeconds = TimelineCameraProjection.GetDurationSeconds(_timelineDocument);
        if (double.IsFinite(CameraTimeNumberBox.Value))
        {
            keyframe.MoveTo(CameraTimeNumberBox.Value, durationSeconds);
        }

        if (double.IsFinite(CameraTranslationXNumberBox.Value))
        {
            keyframe.TranslationX = CameraTranslationXNumberBox.Value;
        }

        if (double.IsFinite(CameraTranslationYNumberBox.Value))
        {
            keyframe.TranslationY = CameraTranslationYNumberBox.Value;
        }

        if (double.IsFinite(CameraTranslationZNumberBox.Value))
        {
            keyframe.TranslationZ = CameraTranslationZNumberBox.Value;
        }

        if (double.IsFinite(CameraRotationXNumberBox.Value))
        {
            keyframe.RotationX = CameraRotationXNumberBox.Value;
        }

        if (double.IsFinite(CameraRotationYNumberBox.Value))
        {
            keyframe.RotationY = CameraRotationYNumberBox.Value;
        }

        if (double.IsFinite(CameraRotationZNumberBox.Value))
        {
            keyframe.RotationZ = CameraRotationZNumberBox.Value;
        }

        if (double.IsFinite(CameraZoomNumberBox.Value))
        {
            keyframe.Zoom = Math.Max(0.001, CameraZoomNumberBox.Value);
        }

        if (double.IsFinite(CameraFovNumberBox.Value))
        {
            keyframe.Fov = Math.Max(0.001, CameraFovNumberBox.Value);
        }

        await CommitCameraKeyframesAsync(
            before,
            _cameraKeyframes,
            "camera keyframe edited",
            keyframe.StableId);
    }

    private async Task MoveSelectedCameraToPlayheadAsync()
    {
        TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
        if (_timelineDocument is null || keyframe is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        keyframe.MoveTo(
            _positionSeconds,
            TimelineCameraProjection.GetDurationSeconds(_timelineDocument));
        await CommitCameraKeyframesAsync(
            before,
            _cameraKeyframes,
            "camera keyframe moved to playhead",
            keyframe.StableId);
    }

    private async Task QuantizeSelectedCameraKeyframeAsync()
    {
        TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
        if (_timelineDocument is null || keyframe is null)
        {
            return;
        }

        if (!TryGetSnapGridSeconds(out double gridSeconds))
        {
            ShowInfo(
                "Choose a snap grid before quantizing a camera keyframe.",
                InfoBarSeverity.Warning);
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        keyframe.Quantize(
            gridSeconds,
            TimelineCameraProjection.GetDurationSeconds(_timelineDocument));
        await CommitCameraKeyframesAsync(
            before,
            _cameraKeyframes,
            "camera keyframe quantized",
            keyframe.StableId);
    }

    private async Task DuplicateSelectedCameraKeyframeAsync()
    {
        TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
        if (_timelineDocument is null || keyframe is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        double? durationSeconds = TimelineCameraProjection.GetDurationSeconds(_timelineDocument);
        TimelineCameraKeyframeDocument duplicate = TimelineCameraProjection.Duplicate(
            keyframe,
            durationSeconds);
        duplicate.MoveTo(SnapTime(_positionSeconds), durationSeconds);
        var updated = _cameraKeyframes.ToList();
        updated.Add(duplicate);
        await CommitCameraKeyframesAsync(
            before,
            updated,
            "camera keyframe duplicated",
            duplicate.StableId);
    }

    private async Task DeleteSelectedCameraKeyframeAsync()
    {
        TimelineCameraKeyframeDocument? keyframe = SelectedCameraKeyframe;
        if (_timelineDocument is null || keyframe is null)
        {
            return;
        }

        if (!await ConfirmAsync(
                "Delete camera keyframe?",
                $"Delete the camera keyframe at {keyframe.TimeSeconds:0.###} seconds?",
                "Delete"))
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        var updated = _cameraKeyframes
            .Where(candidate => candidate.StableId != keyframe.StableId)
            .ToList();
        await CommitCameraKeyframesAsync(
            before,
            updated,
            "camera keyframe deleted",
            selectionId: null);
    }

    private async void Clip_PointerPressed(object sender, PointerRoutedEventArgs e)
    {
        if (sender is not Border border ||
            border.Tag is not string stableId ||
            _timelineDocument is null)
        {
            return;
        }

        TimelineLaneDocument? lane = _lanes.FirstOrDefault(item => item.StableId == stableId);
        if (lane is null || IsLaneLocked(lane))
        {
            if (lane is not null)
            {
                SelectLane(stableId);
                ShowInfo("Unlock this track before editing its clips.", InfoBarSeverity.Warning);
            }
            return;
        }

        SelectLane(stableId);
        if (_pointerTool == TimelinePointerTool.Blade)
        {
            double splitSeconds = Math.Clamp(
                SnapTime(e.GetCurrentPoint(TimelineCanvas).Position.X / _pixelsPerSecond),
                lane.StartSeconds,
                lane.EndSeconds);
            SetPosition(splitSeconds, requestPreview: false);
            e.Handled = true;
            try
            {
                await SplitLaneAtAsync(lane, splitSeconds);
            }
            catch (ArgumentOutOfRangeException ex)
            {
                ShowInfo(ex.Message, InfoBarSeverity.Warning);
            }

            return;
        }

        var localPoint = e.GetCurrentPoint(border);
        _dragMode = localPoint.Position.X <= 8
            ? DragMode.TrimStart
            : localPoint.Position.X >= border.ActualWidth - 8
                ? DragMode.TrimEnd
                : DragMode.Move;
        _dragOriginalLane = lane;
        _dragProvisionalLane = lane;
        _dragBeforeSnapshot = CloneDocument(_timelineDocument);
        _dragBorder = border;
        _dragPointerId = e.Pointer.PointerId;
        _dragStartPoint = e.GetCurrentPoint(TimelineCanvas).Position;
        border.CapturePointer(e.Pointer);
        e.Handled = true;
        await RefreshPreviewAsync(force: false);
    }

    private void Clip_PointerMoved(object sender, PointerRoutedEventArgs e)
    {
        if (_dragBorder is null ||
            _dragOriginalLane is null ||
            e.Pointer.PointerId != _dragPointerId)
        {
            return;
        }

        Point current = e.GetCurrentPoint(TimelineCanvas).Position;
        double deltaSeconds = (current.X - _dragStartPoint.X) / _pixelsPerSecond;
        TimelineLaneDocument candidate;
        try
        {
            switch (_dragMode)
            {
                case DragMode.TrimStart:
                    candidate = TimelineProjection.Trim(
                        _dragOriginalLane,
                        SnapTime(_dragOriginalLane.StartSeconds + deltaSeconds),
                        _dragOriginalLane.EndSeconds,
                        _durationSeconds);
                    break;
                case DragMode.TrimEnd:
                    candidate = TimelineProjection.Trim(
                        _dragOriginalLane,
                        _dragOriginalLane.StartSeconds,
                        SnapTime(_dragOriginalLane.EndSeconds + deltaSeconds),
                        _durationSeconds);
                    break;
                default:
                    candidate = TimelineProjection.Move(
                        _dragOriginalLane,
                        SnapTime(_dragOriginalLane.StartSeconds + deltaSeconds),
                        _durationSeconds);
                    if (!_dragOriginalLane.IsLayer)
                    {
                        int trackIndex = Math.Clamp(
                            (int)Math.Floor(current.Y / TrackHeight),
                            0,
                            Math.Max(0, TrackCount - 1));
                        JsonObject? timelineDocument = _timelineDocument;
                        if (timelineDocument is null ||
                            TimelineProjection.IsTrackLocked(timelineDocument, trackIndex))
                        {
                            return;
                        }
                        candidate = TimelineProjection.ReassignTrack(candidate, trackIndex);
                    }
                    break;
            }
        }
        catch (ArgumentOutOfRangeException)
        {
            return;
        }

        _dragProvisionalLane = candidate;
        Canvas.SetLeft(_dragBorder, candidate.StartSeconds * _pixelsPerSecond);
        Canvas.SetTop(
            _dragBorder,
            ((candidate.IsLayer ? OverlayVisualTrackIndex : candidate.TrackIndex) * TrackHeight) +
            ClipVerticalInset);
        _dragBorder.Width = Math.Max(
            8,
            (candidate.EndSeconds - candidate.StartSeconds) * _pixelsPerSecond);
        e.Handled = true;
    }

    private async void Clip_PointerReleased(object sender, PointerRoutedEventArgs e)
    {
        if (_dragBorder is null ||
            _dragOriginalLane is null ||
            _dragProvisionalLane is null ||
            _dragBeforeSnapshot is null ||
            e.Pointer.PointerId != _dragPointerId)
        {
            return;
        }

        Border border = _dragBorder;
        TimelineLaneDocument original = _dragOriginalLane;
        TimelineLaneDocument provisional = _dragProvisionalLane;
        JsonObject before = _dragBeforeSnapshot;
        ResetDragState();
        border.ReleasePointerCapture(e.Pointer);

        if (LaneGeometryEquals(original, provisional))
        {
            RenderTimeline();
            return;
        }

        ReplaceLaneByStableId(original.StableId, provisional);
        if (_rippleEnabled && !original.IsLayer && original.TrackIndex == provisional.TrackIndex)
        {
            _lanes = TimelineProjection.RippleAfterEdit(
                _lanes,
                original,
                provisional,
                _durationSeconds);
        }
        await CommitLanesAsync(
            before,
            provisional.IsLayer ? "timeline overlay edited" : "timeline clip edited",
            provisional.StableId);
        e.Handled = true;
    }

    private void Clip_PointerCanceled(object sender, PointerRoutedEventArgs e)
    {
        if (_dragBorder is null || e.Pointer.PointerId != _dragPointerId)
        {
            return;
        }

        ResetDragState();
        RenderTimeline();
    }

    private void ResetDragState()
    {
        _dragOriginalLane = null;
        _dragProvisionalLane = null;
        _dragBeforeSnapshot = null;
        _dragBorder = null;
        _dragPointerId = 0;
        _dragMode = DragMode.None;
    }

    private static bool LaneGeometryEquals(
        TimelineLaneDocument left,
        TimelineLaneDocument right) =>
        Math.Abs(left.StartSeconds - right.StartSeconds) < 0.0001 &&
        Math.Abs(left.EndSeconds - right.EndSeconds) < 0.0001 &&
        left.TrackIndex == right.TrackIndex;

    private bool IsLaneLocked(TimelineLaneDocument? lane) =>
        lane is { IsLayer: false } &&
        _timelineDocument is not null &&
        TimelineProjection.IsTrackLocked(_timelineDocument, lane.TrackIndex);

    private void ReplaceLaneByStableId(
        string stableId,
        TimelineLaneDocument replacement)
    {
        var updated = _lanes.ToList();
        int index = updated.FindIndex(lane => lane.StableId == stableId);
        if (index >= 0)
        {
            updated[index] = replacement;
            _lanes = updated;
        }
    }

    private async Task CommitLanesAsync(
        JsonObject before,
        string reason,
        string? selectionId)
    {
        if (_timelineDocument is null)
        {
            return;
        }

        _timelineDocument = TimelineProjection.Rebuild(_timelineDocument, _lanes);
        PushUndo(before);
        _selectedLaneId = selectionId;
        _selectedCameraKeyframeIdentity = null;
        _isDirty = true;
        RefreshEditor(updateRawText: true);
        await AutosaveAsync(reason);
        await RefreshPreviewAsync(force: false);
    }

    private async Task CommitDocumentAsync(
        JsonObject before,
        JsonObject document,
        string reason,
        string? selectionId = null,
        string? cameraSelectionId = null)
    {
        _timelineDocument = document;
        PushUndo(before);
        _selectedLaneId = selectionId;
        _selectedCameraKeyframeIdentity = cameraSelectionId;
        _isDirty = true;
        RefreshEditor(updateRawText: true);
        await AutosaveAsync(reason);
        await RefreshPreviewAsync(force: false);
    }

    private Task CommitCameraKeyframesAsync(
        JsonObject before,
        IEnumerable<TimelineCameraKeyframeDocument> keyframes,
        string reason,
        string? selectionId)
    {
        if (_timelineDocument is null)
        {
            return Task.CompletedTask;
        }

        JsonObject rebuilt = TimelineCameraProjection.Rebuild(_timelineDocument, keyframes);
        return CommitDocumentAsync(
            before,
            rebuilt,
            reason,
            selectionId: null,
            cameraSelectionId: selectionId);
    }

    private void PushUndo(JsonObject snapshot)
    {
        _undoHistory.Add(CloneDocument(snapshot));
        if (_undoHistory.Count > HistoryLimit)
        {
            _undoHistory.RemoveAt(0);
        }

        _redoHistory.Clear();
    }

    private static JsonObject CloneDocument(JsonObject source) =>
        source.DeepClone() as JsonObject
        ?? throw new InvalidOperationException("Timeline cloning failed.");

    private static JsonElement ToJsonElement(JsonObject source) =>
        JsonDocument.Parse(source.ToJsonString()).RootElement.Clone();

    private async Task AutosaveAsync(string reason)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            return;
        }

        try
        {
            JsonElement metadata = ToJsonElement(new JsonObject
            {
                ["editor"] = "winui",
                ["selected_clip_id"] = _selectedLaneId,
                ["selected_camera_keyframe_id"] = _selectedCameraKeyframeIdentity
            });
            await App.Services.ApiClient.AutosaveTimelineAsync(
                _loadedProjectId,
                ToJsonElement(_timelineDocument),
                metadata,
                reason,
                StudioPageHelpers.ExpectedRevision(_project),
                _pageCancellation?.Token ?? CancellationToken.None);
            await RefreshProjectRevisionAsync(
                _loadedProjectId,
                _pageCancellation?.Token ?? CancellationToken.None);
            StatusText.Text = "Autosaved.";
            await RefreshRecoveryAsync();
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            ShowInfo($"Autosave failed: {ex.Message}", InfoBarSeverity.Warning);
        }
    }

    private async void Undo_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || _undoHistory.Count == 0)
        {
            return;
        }

        _redoHistory.Add(CloneDocument(_timelineDocument));
        JsonObject snapshot = _undoHistory[^1];
        _undoHistory.RemoveAt(_undoHistory.Count - 1);
        _timelineDocument = CloneDocument(snapshot);
        _isDirty = true;
        RefreshEditor(updateRawText: true);
        await AutosaveAsync("timeline undo");
        await RefreshPreviewAsync(force: false);
    }

    private async void Redo_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || _redoHistory.Count == 0)
        {
            return;
        }

        _undoHistory.Add(CloneDocument(_timelineDocument));
        JsonObject snapshot = _redoHistory[^1];
        _redoHistory.RemoveAt(_redoHistory.Count - 1);
        _timelineDocument = CloneDocument(snapshot);
        _isDirty = true;
        RefreshEditor(updateRawText: true);
        await AutosaveAsync("timeline redo");
        await RefreshPreviewAsync(force: false);
    }

    private async void SaveTimeline_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            return;
        }

        SetBusy(true);
        try
        {
            await SaveTimelineDocumentAsync(_pageCancellation?.Token ?? CancellationToken.None);
            ShowInfo("Timeline changes were saved to the project.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async Task SaveTimelineDocumentAsync(CancellationToken cancellationToken)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            throw new InvalidOperationException("Load a project Timeline before saving.");
        }

        await App.Services.ApiClient.SaveTimelineAsync(
            _loadedProjectId,
            ToJsonElement(_timelineDocument),
            StudioPageHelpers.ExpectedRevision(_project),
            cancellationToken);
        await RefreshProjectRevisionAsync(_loadedProjectId, cancellationToken);
        _isDirty = false;
        StatusText.Text = "Timeline saved.";
        await RefreshRecoveryAsync();
    }

    private async void RefreshWorkflow_Click(object sender, RoutedEventArgs e)
    {
        RefreshWorkflowPlanSummary();
        await RunAutomationAsync(
            "Refreshing project sources...",
            async token =>
            {
                await LoadWorkflowAssetsAsync(token);
                return $"Loaded {SourceAssetComboBox.Items.Count} project sources.";
            });
    }

    private async void AppendPlan_Click(object sender, RoutedEventArgs e) =>
        await ApplyWorkspacePlanAsync(overwrite: false);

    private async void OverwritePlan_Click(object sender, RoutedEventArgs e)
    {
        if (!await ConfirmAsync(
                "Overwrite Timeline from plan?",
                $"Replace the current Timeline with Workspace plan variant {_loadedVariantIndex + 1}? " +
                "The replacement remains undoable until you leave this project.",
                "Overwrite Timeline"))
        {
            return;
        }

        await ApplyWorkspacePlanAsync(overwrite: true);
    }

    private bool _hasAiEditProposal;

    private void ResetAiEditProposal()
    {
        _hasAiEditProposal = false;
        AiEditProposalText.Text = "No AI edit proposal has been generated.";
    }

    private async void GenerateAiEdit_Click(object sender, RoutedEventArgs e)
    {
        string instruction = AiEditInstructionTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(instruction))
        {
            ShowAutomationInfo("Describe the edit you want before generating a proposal.", InfoBarSeverity.Warning);
            AiEditInstructionTextBox.Focus(FocusState.Programmatic);
            return;
        }

        if (string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            ShowAutomationInfo("Load a project before generating an AI edit proposal.", InfoBarSeverity.Warning);
            return;
        }

        int maximumScenes = double.IsFinite(AiEditMaximumScenesNumberBox.Value)
            ? Math.Clamp((int)AiEditMaximumScenesNumberBox.Value, 1, 64)
            : 12;
        _hasAiEditProposal = false;
        AiEditProposalText.Text = "Generating a proposal...";
        UpdateCommandState();

        await RunAutomationAsync(
            "Generating a non-destructive AI edit proposal...",
            async token =>
            {
                if (_isDirty)
                {
                    await SaveTimelineDocumentAsync(token);
                }

                var request = new PlanRequest(
                    _project?.Name,
                    instruction,
                    "timeline edit; preserve source intent; return practical shot timing and editable prompts",
                    1,
                    maximumScenes,
                    StudioPageHelpers.ExpectedRevision(_project));
                PlanDto plan = await App.Services.ApiClient.GeneratePlanAsync(
                    _loadedProjectId,
                    request,
                    "creative",
                    token);
                await RefreshProjectRevisionAsync(_loadedProjectId, token);
                PlanVariantDto variant = plan.Variants.FirstOrDefault()
                    ?? throw new InvalidOperationException("The planner returned no editable variants.");

                _loadedVariantIndex = 0;
                _hasAiEditProposal = true;
                AiEditProposalText.Text = FormatAiEditProposal(variant);
                RefreshWorkflowPlanSummary();
                return $"Proposal ready with {variant.SceneCount} scene{(variant.SceneCount == 1 ? string.Empty : "s")}. Review it before appending or replacing.";
            });
    }

    private async void AppendAiEdit_Click(object sender, RoutedEventArgs e)
    {
        if (_hasAiEditProposal)
        {
            await ApplyWorkspacePlanAsync(overwrite: false);
        }
    }

    private async void ReplaceWithAiEdit_Click(object sender, RoutedEventArgs e)
    {
        if (!_hasAiEditProposal ||
            !await ConfirmAsync(
                "Replace Timeline with AI proposal?",
                "Replace the current generated Timeline content with the reviewed AI edit proposal? " +
                "Locked tracks are preserved and the replacement remains undoable until you leave this project.",
                "Replace Timeline"))
        {
            return;
        }

        await ApplyWorkspacePlanAsync(overwrite: true);
    }

    private static string FormatAiEditProposal(PlanVariantDto variant)
    {
        var lines = new List<string>
        {
            $"{variant.DisplayName} · {variant.SceneCount} scenes" +
            (variant.DurationSeconds is double duration ? $" · {duration:0.##} s" : string.Empty)
        };
        if (!string.IsNullOrWhiteSpace(variant.Logline))
        {
            lines.Add(variant.Logline!);
        }

        lines.AddRange(variant.Scenes.Select((scene, index) =>
            $"{index + 1}. {scene.StartSeconds:0.##}–{scene.EndSeconds:0.##} s · " +
            (string.IsNullOrWhiteSpace(scene.Prompt) ? "Editable scene" : scene.Prompt)));
        return string.Join(Environment.NewLine, lines);
    }

    private async Task ApplyWorkspacePlanAsync(bool overwrite)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            ShowAutomationInfo("Load a project Timeline before applying a Workspace plan.", InfoBarSeverity.Warning);
            return;
        }

        await RunAutomationAsync(
            overwrite ? "Replacing Timeline from Workspace plan..." : "Appending Workspace plan...",
            async token =>
            {
                if (!overwrite && _isDirty)
                {
                    await SaveTimelineDocumentAsync(token);
                }

                JsonObject before = CloneDocument(_timelineDocument);
                ApplyPlanToTimelineResponse response =
                    await App.Services.ApiClient.ApplyPlanToTimelineAsync(
                        _loadedProjectId,
                        _loadedVariantIndex,
                        overwrite,
                        StudioPageHelpers.ExpectedRevision(_project),
                        token);
                if (!response.Ok)
                {
                    throw new InvalidOperationException("The backend did not apply the Workspace plan.");
                }
                await RefreshProjectRevisionAsync(_loadedProjectId, token);

                JsonObject result = TimelineProjection.PreserveLockedTracks(
                    before,
                    ExtractTimeline(response.Timeline));
                await CommitDocumentAsync(
                    before,
                    result,
                    overwrite ? "plan overwrite" : "plan append",
                    _selectedLaneId);
                SetAutomationResult(result);
                return $"Workspace plan variant {response.VariantIndex + 1} " +
                    (overwrite ? "replaced the Timeline." : "was appended to the Timeline.");
            });
    }

    private void SourceAssetComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (SourceAssetComboBox.SelectedItem is string sourcePath)
        {
            SelectSource(sourcePath);
        }
    }

    private async void BrowseSource_Click(object sender, RoutedEventArgs e)
    {
        if (App.MainWindowInstance is null)
        {
            ShowAutomationInfo("The Studio window is not ready for file selection.", InfoBarSeverity.Error);
            return;
        }

        var picker = new FileOpenPicker
        {
            SuggestedStartLocation = PickerLocationId.VideosLibrary,
            ViewMode = PickerViewMode.List,
        };
        foreach (string extension in new[]
                 {
                     ".mp4", ".mov", ".mkv", ".avi", ".webm",
                     ".wav", ".mp3", ".flac", ".aac", ".m4a", ".ogg",
                     ".png", ".jpg", ".jpeg", ".webp",
                 })
        {
            picker.FileTypeFilter.Add(extension);
        }

        nint windowHandle = WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindowInstance);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle);
        StorageFile? file = await picker.PickSingleFileAsync();
        if (file is not null)
        {
            SourceAssetComboBox.SelectedItem = null;
            SelectSource(file.Path);
        }
    }

    private async void AssignSource_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetAutomationContext(requireSelection: true, out JsonObject timeline))
        {
            return;
        }

        await RunAutomationAsync(
            "Assigning source...",
            async token =>
            {
                token.ThrowIfCancellationRequested();
                JsonObject before = CloneDocument(_timelineDocument!);
                TimelineAutomationResult result =
                    TimelineAutomation.AssignSource(timeline, _selectedLaneId!, _selectedSourcePath);
                await CommitDocumentAsync(before, result.Timeline, "source assignment", _selectedLaneId);
                SetAutomationResult(result.Timeline);
                return result.Summary;
            });
    }

    private async void AddSourceClip_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetAutomationContext(requireSelection: false, out JsonObject timeline))
        {
            return;
        }

        if (!TryReadFinite(NewClipDurationNumberBox, out double duration))
        {
            ShowAutomationInfo("Enter a valid source clip duration.", InfoBarSeverity.Warning);
            return;
        }

        int trackIndex = Math.Max(0, (int)Math.Round(ReadFiniteOrDefault(TrackNumberBox, 1)) - 1);
        if (TimelineProjection.IsTrackLocked(timeline, trackIndex))
        {
            ShowAutomationInfo($"Track {trackIndex + 1} is locked.", InfoBarSeverity.Warning);
            return;
        }

        await RunAutomationAsync(
            "Adding source clip...",
            async token =>
            {
                token.ThrowIfCancellationRequested();
                JsonObject before = CloneDocument(_timelineDocument!);
                TimelineAutomationResult result = TimelineAutomation.AddSourceClip(
                    timeline,
                    _selectedSourcePath,
                    _positionSeconds,
                    duration,
                    trackIndex);
                await CommitDocumentAsync(before, result.Timeline, "source clip insertion", _selectedLaneId);
                SetAutomationResult(result.Timeline);
                return result.Summary;
            });
    }

    private async void SequenceTrack_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null)
        {
            ShowAutomationInfo("Load a Timeline before sequencing a track.", InfoBarSeverity.Warning);
            return;
        }

        if (!TryReadFinite(SequenceTrackNumberBox, out double trackNumber) ||
            !TryReadFinite(SequenceStartNumberBox, out double startSeconds) ||
            !TryReadFinite(SequenceGapNumberBox, out double gapSeconds))
        {
            ShowAutomationInfo("Enter valid sequencing values.", InfoBarSeverity.Warning);
            return;
        }

        int trackIndex = Math.Max(0, (int)Math.Round(trackNumber) - 1);
        if (TimelineProjection.IsTrackLocked(_timelineDocument, trackIndex))
        {
            ShowAutomationInfo($"Track {trackIndex + 1} is locked.", InfoBarSeverity.Warning);
            return;
        }

        await RunAutomationAsync(
            "Sequencing track...",
            async token =>
            {
                token.ThrowIfCancellationRequested();
                JsonObject before = CloneDocument(_timelineDocument);
                TimelineAutomationResult result =
                    TimelineAutomation.SequenceTrack(_timelineDocument, trackIndex, startSeconds, gapSeconds);
                await CommitDocumentAsync(before, result.Timeline, "track sequencing", _selectedLaneId);
                SetAutomationResult(result.Timeline);
                return result.Summary;
            });
    }

    private async void ApplyMotion_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            ShowAutomationInfo("Load a project Timeline before applying motion grammar.", InfoBarSeverity.Warning);
            return;
        }

        if (!TryReadFinite(MotionStartNumberBox, out double startSeconds) ||
            !TryReadFinite(MotionEndNumberBox, out double endSeconds) ||
            endSeconds <= startSeconds)
        {
            ShowAutomationInfo("Motion end time must be later than its start time.", InfoBarSeverity.Warning);
            return;
        }

        string phrase = GetSelectedTag(MotionPhraseComboBox) ?? "prepare";
        await RunAutomationAsync(
            "Applying motion grammar...",
            async token =>
            {
                if (_isDirty)
                {
                    await SaveTimelineDocumentAsync(token);
                }

                JsonObject before = CloneDocument(_timelineDocument);
                ApplyMotionGrammarResponse response =
                    await App.Services.ApiClient.ApplyMotionGrammarAsync(
                        _loadedProjectId,
                        [new MotionPhraseRequest(phrase, startSeconds, endSeconds)],
                        OverwriteMotionToggle.IsOn,
                        StudioPageHelpers.ExpectedRevision(_project),
                        token);
                if (!response.Ok)
                {
                    throw new InvalidOperationException("The backend did not apply the motion grammar.");
                }
                await RefreshProjectRevisionAsync(_loadedProjectId, token);

                JsonObject result = TimelineProjection.PreserveLockedTracks(
                    before,
                    ExtractTimeline(response.Timeline));
                await CommitDocumentAsync(before, result, "motion grammar", _selectedLaneId);
                SetAutomationResult(result);
                return $"Applied the {phrase} motion phrase from {startSeconds:0.###} s to {endSeconds:0.###} s.";
            });
    }

    private void CancelAutomation_Click(object sender, RoutedEventArgs e) =>
        _automationCancellation?.Cancel();

    private async void OpenWorkspace_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("workspace");

    private async void OpenRender_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("render");

    private async void OpenReview_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("review");

    private async void OpenOutputs_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("outputs");

    private async void OpenQueue_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("queue");

    private async void OpenPlanner_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("plannerLab");

    private async void OpenReactive_Click(object sender, RoutedEventArgs e) =>
        await NavigateWithSaveAsync("reactiveLab");

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        if (_isDirty &&
            !await ConfirmAsync(
                "Reload timeline?",
                "Reloading discards unsaved local edits. Autosaved recovery data remains available.",
                "Reload"))
        {
            return;
        }

        await LoadActiveProjectAsync();
    }

    private async void AddClipAtPlayhead_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetNewLaneRange(out double start, out double end) || _timelineDocument is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        int trackIndex = SelectedLane is { IsLayer: false } selected ? selected.TrackIndex : 0;
        if (TimelineProjection.IsTrackLocked(_timelineDocument, trackIndex))
        {
            ShowInfo("Unlock the destination track before adding a clip.", InfoBarSeverity.Warning);
            return;
        }

        TimelineLaneDocument clip = TimelineProjection.CreateLane(
            $"Clip {_lanes.Count(lane => !lane.IsLayer) + 1}",
            "video",
            start,
            end);
        clip = TimelineProjection.ReassignTrack(clip, trackIndex);
        _lanes = [.. _lanes, clip];
        await CommitLanesAsync(before, "timeline clip add", clip.StableId);
    }

    private async void AddOverlayAtPlayhead_Click(object sender, RoutedEventArgs e)
    {
        if (!TryGetNewLaneRange(out double start, out double end) || _timelineDocument is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        TimelineLaneDocument overlay = TimelineProjection.CreateLayer(
            $"Overlay {_lanes.Count(lane => lane.IsLayer) + 1}",
            "overlay",
            start,
            end);
        _lanes = [.. _lanes, overlay];
        await CommitLanesAsync(before, "timeline overlay add", overlay.StableId);
    }

    private bool TryGetNewLaneRange(out double start, out double end)
    {
        start = 0;
        end = 0;
        if (_timelineDocument is null ||
            _durationSeconds < TimelineProjection.MinimumDurationSeconds)
        {
            ShowInfo("The Timeline duration is too short to add an item.", InfoBarSeverity.Warning);
            return false;
        }

        start = Math.Clamp(
            _positionSeconds,
            0,
            _durationSeconds - TimelineProjection.MinimumDurationSeconds);
        end = Math.Min(_durationSeconds, start + 1);
        return true;
    }

    private async void ApplyInspector_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        double track = 1;
        if (!TryReadFinite(StartNumberBox, out double start) ||
            !TryReadFinite(EndNumberBox, out double end) ||
            !TryReadFinite(SourceInNumberBox, out double sourceIn) ||
            !TryReadFinite(SourceOutNumberBox, out double sourceOut) ||
            !TryReadFinite(SpeedNumberBox, out double speed) ||
            (!lane.IsLayer && !TryReadFinite(TrackNumberBox, out track)) ||
             !TryReadFinite(VolumeNumberBox, out double volume) ||
             !TryReadFinite(FadeInNumberBox, out double fadeIn) ||
             !TryReadFinite(FadeOutNumberBox, out double fadeOut) ||
             !TryReadFinite(OpacityNumberBox, out double opacity) ||
             !TryReadFinite(BrightnessNumberBox, out double brightness) ||
             !TryReadFinite(ContrastNumberBox, out double contrast) ||
             !TryReadFinite(SaturationNumberBox, out double saturation))
        {
            ShowInfo("Inspector values must be finite numbers.", InfoBarSeverity.Warning);
            return;
        }

        if (start < 0 ||
            end - start < TimelineProjection.MinimumDurationSeconds ||
            end > _durationSeconds ||
            sourceIn < 0 ||
            sourceOut < 0 ||
            speed is < 0.25 or > 4 ||
            (!lane.IsLayer && track < 1) ||
             volume is < 0 or > 2 ||
             fadeIn < 0 ||
             fadeOut < 0 ||
             opacity is < 0 or > 1 ||
             brightness is < -1 or > 1 ||
             contrast is < 0 or > 2 ||
             saturation is < 0 or > 3)
        {
            ShowInfo(
                "Check the clip range, track, speed, volume, source, fade, and video-adjustment values.",
                InfoBarSeverity.Warning);
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        TimelineLaneDocument updated = TimelineProjection.Trim(
            lane,
            start,
            end,
            _durationSeconds);
        updated.Name = string.IsNullOrWhiteSpace(LaneNameTextBox.Text)
            ? lane.Name
            : LaneNameTextBox.Text.Trim();
        updated.Type = string.IsNullOrWhiteSpace(LaneTypeTextBox.Text)
            ? lane.Type
            : LaneTypeTextBox.Text.Trim();
        updated.SourcePath = SourcePathTextBox.Text.Trim();
        updated.SourceInSeconds = sourceIn;
        updated.SourceOutSeconds = sourceOut;
        updated.Speed = speed;
        updated.Volume = volume;
        updated.Muted = MutedToggle.IsOn;
        updated.FadeInSeconds = fadeIn;
        updated.FadeOutSeconds = fadeOut;
        if (IsVisualLane(updated))
        {
            updated.FitMode = GetSelectedTag(FitModeComboBox) ?? "contain";
            updated.Opacity = opacity;
            updated.Brightness = brightness;
            updated.Contrast = contrast;
            updated.Saturation = saturation;
            updated.RotationDegrees = int.TryParse(
                GetSelectedTag(RotationComboBox),
                NumberStyles.Integer,
                CultureInfo.InvariantCulture,
                out int rotationDegrees)
                    ? rotationDegrees
                    : 0;
            updated.FlipHorizontal = FlipHorizontalToggle.IsOn;
        }
        if (!lane.IsLayer)
        {
            int destinationTrack =
                Math.Max(0, (int)Math.Round(track, MidpointRounding.AwayFromZero) - 1);
            if (TimelineProjection.IsTrackLocked(_timelineDocument, destinationTrack))
            {
                ShowInfo(
                    "Unlock the destination track before moving a clip there.",
                    InfoBarSeverity.Warning);
                return;
            }

            updated = TimelineProjection.ReassignTrack(updated, destinationTrack);
        }

        ReplaceLaneByStableId(lane.StableId, updated);
        if (!updated.IsLayer)
        {
            foreach (TimelineLaneDocument trackLane in _lanes.Where(item =>
                         !item.IsLayer &&
                         item.TrackIndex == updated.TrackIndex))
            {
                trackLane.Type = updated.Type;
            }
        }
        if (_rippleEnabled &&
            !lane.IsLayer &&
            lane.TrackIndex == updated.TrackIndex)
        {
            _lanes = TimelineProjection.RippleAfterEdit(
                _lanes,
                lane,
                updated,
                _durationSeconds);
        }

        await CommitLanesAsync(
            before,
            updated.IsLayer ? "timeline overlay inspector edit" : "timeline clip inspector edit",
            updated.StableId);
    }

    private void ApplyVideoLook_Click(object sender, RoutedEventArgs e)
    {
        switch (GetSelectedTag(VideoLookComboBox) ?? "neutral")
        {
            case "punchy":
                BrightnessNumberBox.Value = 0.03;
                ContrastNumberBox.Value = 1.18;
                SaturationNumberBox.Value = 1.25;
                break;
            case "soft":
                BrightnessNumberBox.Value = 0.04;
                ContrastNumberBox.Value = 0.9;
                SaturationNumberBox.Value = 0.85;
                break;
            case "monochrome":
                BrightnessNumberBox.Value = 0;
                ContrastNumberBox.Value = 1.05;
                SaturationNumberBox.Value = 0;
                break;
            default:
                BrightnessNumberBox.Value = 0;
                ContrastNumberBox.Value = 1;
                SaturationNumberBox.Value = 1;
                break;
        }
    }

    private void ResetVideoAdjustments_Click(object sender, RoutedEventArgs e)
    {
        SelectComboByTag(FitModeComboBox, "contain");
        SelectComboByTag(RotationComboBox, "0");
        SelectComboByTag(VideoLookComboBox, "neutral");
        OpacityNumberBox.Value = 1;
        BrightnessNumberBox.Value = 0;
        ContrastNumberBox.Value = 1;
        SaturationNumberBox.Value = 1;
        FlipHorizontalToggle.IsOn = false;
    }

    private async Task SplitLaneAtAsync(TimelineLaneDocument lane, double splitSeconds)
    {
        if (_timelineDocument is null)
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        var (left, right) = TimelineProjection.Split(lane, splitSeconds);
        var updated = _lanes.ToList();
        int index = updated.FindIndex(item => item.StableId == lane.StableId);
        if (index < 0)
        {
            return;
        }

        updated[index] = left;
        updated.Insert(index + 1, right);
        _lanes = updated;
        await CommitLanesAsync(
            before,
            lane.IsLayer ? "timeline overlay split" : "timeline clip split",
            right.StableId);
    }

    private async void SplitClip_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        try
        {
            await SplitLaneAtAsync(lane, _positionSeconds);
        }
        catch (ArgumentOutOfRangeException ex)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Warning);
        }
    }

    private async void DuplicateClip_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedCameraKeyframeIdentity is not null)
        {
            await DuplicateSelectedCameraKeyframeAsync();
            return;
        }

        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        TimelineLaneDocument duplicate = TimelineProjection.DuplicateAt(
            lane,
            SnapTime(_positionSeconds),
            _durationSeconds);
        _lanes = [.. _lanes, duplicate];
        await CommitLanesAsync(before, "timeline clip duplicated", duplicate.StableId);
    }

    private async void DeleteSelectedClip_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedCameraKeyframeIdentity is not null)
        {
            await DeleteSelectedCameraKeyframeAsync();
            return;
        }

        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        bool isLayer = lane.IsLayer;
        if (!await ConfirmAsync(
            isLayer ? "Delete selected overlay?" : "Delete selected clip?",
            isLayer
                ? $"Delete the “{lane.Name}” overlay from the timeline?"
                : $"Delete “{lane.Name}” from the timeline?",
            "Delete"))
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        _lanes = _rippleEnabled && !lane.IsLayer
            ? TimelineProjection.RippleAfterDelete(_lanes, lane, _durationSeconds)
            : _lanes.Where(item => item.StableId != lane.StableId).ToArray();
        await CommitLanesAsync(before, "timeline clip deleted", selectionId: null);
    }

    private async void MoveSelectedToPlayhead_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedCameraKeyframeIdentity is not null)
        {
            await MoveSelectedCameraToPlayheadAsync();
            return;
        }

        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        TimelineLaneDocument moved = TimelineProjection.Move(
            lane,
            _positionSeconds,
            _durationSeconds);
        ReplaceLaneByStableId(lane.StableId, moved);
        if (_rippleEnabled && !lane.IsLayer)
        {
            _lanes = TimelineProjection.RippleAfterEdit(_lanes, lane, moved, _durationSeconds);
        }
        await CommitLanesAsync(before, "timeline selection moved to playhead", moved.StableId);
    }

    private async void QuantizeSelected_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedCameraKeyframeIdentity is not null)
        {
            await QuantizeSelectedCameraKeyframeAsync();
            return;
        }

        if (_timelineDocument is null ||
            SelectedLane is not TimelineLaneDocument lane ||
            IsLaneLocked(lane))
        {
            return;
        }

        if (!CanQuantizeToCurrentGrid())
        {
            ShowInfo("Turn snap on and choose an available beat or BPM grid first.", InfoBarSeverity.Warning);
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        double minimumDuration = TimelineProjection.MinimumDurationSeconds;
        double maximumStart = Math.Max(0, _durationSeconds - minimumDuration);
        double start = Math.Clamp(SnapTime(lane.StartSeconds), 0, maximumStart);
        double minimumEnd = Math.Min(_durationSeconds, start + minimumDuration);
        double end = Math.Clamp(SnapTime(lane.EndSeconds), minimumEnd, _durationSeconds);
        TimelineLaneDocument quantized = TimelineProjection.Trim(
            lane,
            start,
            end,
            _durationSeconds);
        ReplaceLaneByStableId(lane.StableId, quantized);
        if (_rippleEnabled && !lane.IsLayer)
        {
            _lanes = TimelineProjection.RippleAfterEdit(_lanes, lane, quantized, _durationSeconds);
        }
        await CommitLanesAsync(before, "timeline selection quantized", quantized.StableId);
    }

    private void PlayPause_Click(object sender, RoutedEventArgs e)
    {
        if (_isPlaying)
        {
            StopPlayback();
            return;
        }

        if (_positionSeconds >= _durationSeconds)
        {
            SetPosition(LoopToggle.IsChecked == true ? ResolveLoopBounds().Start : 0, requestPreview: false);
        }

        _transportAnchorSeconds = _positionSeconds;
        _transportWatch.Restart();
        _transportTimer.Start();
        _isPlaying = true;
        UpdateTransportUi();
    }

    private void StepBackward_Click(object sender, RoutedEventArgs e)
    {
        StopPlayback();
        SetPosition(_positionSeconds - (1 / DefaultFps), requestPreview: true);
    }

    private void StepForward_Click(object sender, RoutedEventArgs e)
    {
        StopPlayback();
        SetPosition(_positionSeconds + (1 / DefaultFps), requestPreview: true);
    }

    private void TransportTimer_Tick(object? sender, object e)
    {
        if (!_isPlaying)
        {
            return;
        }

        double position = _transportAnchorSeconds + _transportWatch.Elapsed.TotalSeconds;
        if (LoopToggle.IsChecked == true)
        {
            (double start, double end) = ResolveLoopBounds();
            if (position >= end)
            {
                _transportAnchorSeconds = start;
                _transportWatch.Restart();
                position = start;
            }
        }
        else if (position >= _durationSeconds)
        {
            SetPosition(_durationSeconds, requestPreview: true);
            StopPlayback();
            return;
        }

        SetPosition(position, requestPreview: true);
    }

    private void StopPlayback()
    {
        _transportTimer.Stop();
        _transportWatch.Stop();
        _isPlaying = false;
        UpdateTransportUi();
    }

    private void SetPosition(double position, bool requestPreview)
    {
        _positionSeconds = Math.Clamp(position, 0, _durationSeconds);
        _updatingPosition = true;
        PositionSlider.Value = _positionSeconds;
        _updatingPosition = false;
        UpdateTransportUi();
        RenderPlayhead();
        UpdateSplitCommandState();
        if (requestPreview)
        {
            _ = RefreshPreviewAsync(force: false);
        }
    }

    private void RenderPlayhead()
    {
        if (_playheadLine is null)
        {
            return;
        }

        double x = _positionSeconds * _pixelsPerSecond;
        _playheadLine.X1 = x;
        _playheadLine.X2 = x;
    }

    private void UpdateTransportUi()
    {
        TimecodeText.Text = FormatTimecode(_positionSeconds);
        PlayPauseButton.Content = _isPlaying ? "Pause" : "Play";
    }

    private void PositionSlider_ValueChanged(
        object sender,
        RangeBaseValueChangedEventArgs e)
    {
        if (_updatingPosition || _timelineDocument is null)
        {
            return;
        }

        _positionSeconds = Math.Clamp(e.NewValue, 0, _durationSeconds);
        UpdateTransportUi();
        RenderPlayhead();
        UpdateSplitCommandState();
        if (_positionPointerActive || !_isPlaying)
        {
            _ = RefreshPreviewAsync(force: false);
        }
    }

    private void PositionSlider_PointerPressed(object sender, PointerRoutedEventArgs e)
    {
        _positionPointerActive = true;
        StopPlayback();
    }

    private void PositionSlider_PointerReleased(object sender, PointerRoutedEventArgs e)
    {
        _positionPointerActive = false;
        _ = RefreshPreviewAsync(force: true);
    }

    private void ZoomSlider_ValueChanged(
        object sender,
        RangeBaseValueChangedEventArgs e)
    {
        _pixelsPerSecond = Math.Clamp(
            80 * e.NewValue,
            MinimumPixelsPerSecond,
            MaximumPixelsPerSecond);
        if (_timelineDocument is not null)
        {
            RenderRuler();
            RenderTimeline();
        }
    }

    private void FitTimeline_Click(object sender, RoutedEventArgs e)
    {
        if (_durationSeconds <= 0)
        {
            return;
        }

        double viewport = TimelineScroll.ViewportWidth > 0
            ? TimelineScroll.ViewportWidth
            : 900;
        _pixelsPerSecond = Math.Clamp(
            viewport / _durationSeconds,
            MinimumPixelsPerSecond,
            MaximumPixelsPerSecond);
        ZoomSlider.Value = Math.Clamp(_pixelsPerSecond / 80, 0.25, 4);
        RenderRuler();
        RenderTimeline();
        TimelineScroll.ChangeView(0, null, null, true);
    }

    private void TrackHeaderScroll_ViewChanged(
        object sender,
        ScrollViewerViewChangedEventArgs e)
    {
        if (_syncingScroll)
        {
            return;
        }

        _syncingScroll = true;
        TimelineScroll.ChangeView(
            TimelineScroll.HorizontalOffset,
            TrackHeaderScroll.VerticalOffset,
            null,
            true);
        _syncingScroll = false;
    }

    private void TimelineScroll_ViewChanged(
        object sender,
        ScrollViewerViewChangedEventArgs e)
    {
        if (_syncingScroll)
        {
            return;
        }

        _syncingScroll = true;
        TrackHeaderScroll.ChangeView(
            null,
            TimelineScroll.VerticalOffset,
            null,
            true);
        RulerScroll.ChangeView(
            TimelineScroll.HorizontalOffset,
            null,
            null,
            true);
        _syncingScroll = false;
    }

    private void TimelineCanvas_PointerPressed(object sender, PointerRoutedEventArgs e)
    {
        Point point = e.GetCurrentPoint(TimelineCanvas).Position;
        StopPlayback();
        SelectLane(null);
        SetPosition(SnapTime(point.X / _pixelsPerSecond), requestPreview: true);
    }

    private void SelectToolButton_Click(object sender, RoutedEventArgs e) =>
        SetPointerTool(TimelinePointerTool.Select);

    private void BladeToolButton_Click(object sender, RoutedEventArgs e) =>
        SetPointerTool(TimelinePointerTool.Blade);

    private void SnapCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_isLoaded)
        {
            return;
        }

        RenderTimeline();
        RefreshCameraEditor();
        UpdateCommandState();
    }

    private void RippleToggle_Changed(object sender, RoutedEventArgs e)
    {
        _rippleEnabled = RippleToggle.IsChecked == true;
    }

    private async void TrackLockButton_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || sender is not Button { Tag: int trackIndex })
        {
            return;
        }

        JsonObject before = CloneDocument(_timelineDocument);
        bool locked = !TimelineProjection.IsTrackLocked(_timelineDocument, trackIndex);
        _timelineDocument = TimelineProjection.SetTrackLocked(_timelineDocument, trackIndex, locked);
        await CommitLanesAsync(
            before,
            locked ? "timeline track locked" : "timeline track unlocked",
            _selectedLaneId);
    }

    private bool CanQuantizeToCurrentGrid() =>
        TryGetSnapGridSeconds(out _);

    private bool TryGetSnapGridSeconds(out double gridSeconds)
    {
        gridSeconds = 0;
        string mode = GetSelectedTag(SnapCombo) ?? "off";
        if (string.Equals(mode, "off", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        double bpm = _project?.Bpm is double projectBpm &&
                     double.IsFinite(projectBpm) &&
                     projectBpm > 0
            ? projectBpm
            : 120;
        double beatSeconds = 60 / bpm;
        gridSeconds = mode switch
        {
            "half" => beatSeconds / 2,
            "quarter" => beatSeconds / 4,
            _ => beatSeconds
        };
        return double.IsFinite(gridSeconds) && gridSeconds > 0;
    }

    private double SnapTime(double value)
    {
        double clamped = double.IsFinite(value)
            ? Math.Clamp(value, 0, _durationSeconds)
            : 0;
        if (!TryGetSnapGridSeconds(out double gridSeconds))
        {
            return clamped;
        }

        return Math.Clamp(
            Math.Round(clamped / gridSeconds, MidpointRounding.AwayFromZero) * gridSeconds,
            0,
            _durationSeconds);
    }

    private (double Start, double End) ResolveLoopBounds()
    {
        double start = ReadFiniteOrDefault(LoopInNumberBox, 0);
        double end = ReadFiniteOrDefault(LoopOutNumberBox, _durationSeconds);
        start = Math.Clamp(start, 0, _durationSeconds);
        end = Math.Clamp(end, start + TimelineProjection.MinimumDurationSeconds, _durationSeconds);
        return (start, end);
    }

    private async Task RefreshPreviewAsync(bool force)
    {
        CancelPreview();
        if (_timelineDocument is null ||
            string.IsNullOrWhiteSpace(_loadedProjectId) ||
            !TimelineProjection.HasRenderableVideoClip(_timelineDocument))
        {
            PreviewSurface.ShowUnsupported("No renderable video clip is present at this timeline.");
            PreviewHintText.Text = "Add a video clip with a source path to enable preview.";
            return;
        }

        var cancellation = CancellationTokenSource.CreateLinkedTokenSource(
            _pageCancellation?.Token ?? CancellationToken.None);
        _previewCancellation = cancellation;
        long generation = ++_previewGeneration;
        try
        {
            if (!force)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(90), cancellation.Token);
            }

            PreviewHintText.Text = $"Rendering frame at {FormatClock(_positionSeconds)}...";
            double requestPosition = _positionSeconds;
            await App.Services.ApiClient.StreamTimelineFrameAsync(
                _loadedProjectId,
                requestPosition,
                1280,
                720,
                force,
                async (file, token) =>
                {
                    if (generation != _previewGeneration)
                    {
                        return false;
                    }

                    await PreviewSurface.LoadStreamAsync(
                        file.Stream,
                        file.ContentHeaders.ContentType?.MediaType,
                        token);
                    return true;
                },
                cancellation.Token);
            if (generation == _previewGeneration)
            {
                PreviewHintText.Text = $"Frame {FormatClock(requestPosition)}";
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            if (generation == _previewGeneration)
            {
                PreviewSurface.ShowError(ex.Message);
                PreviewHintText.Text = "Timeline preview failed.";
            }
        }
        finally
        {
            if (ReferenceEquals(_previewCancellation, cancellation))
            {
                _previewCancellation = null;
            }

            cancellation.Dispose();
        }
    }

    private void CancelPreview()
    {
        _previewGeneration++;
        _previewCancellation?.Cancel();
        _previewCancellation = null;
    }

    private async void RenderMaster_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null || string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            return;
        }

        string name = OutputNameTextBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(name))
        {
            ShowInfo("Enter an output name before rendering.", InfoBarSeverity.Warning);
            return;
        }

        string mode = GetSelectedTag(ModeComboBox) ?? "final";
        string aspect = GetSelectedTag(AspectRatioComboBox) ?? "16:9";
        (int width, int height) = ResolveRenderDimensions(mode, aspect);
        int quality = string.Equals(mode, "preview", StringComparison.OrdinalIgnoreCase)
            ? 23
            : 18;

        SetBusy(true);
        StatusText.Text = "Queueing timeline render...";
        try
        {
            var request = new TimelineRenderRequest(
                width,
                height,
                DefaultFps,
                "h264",
                "aac",
                quality,
                name);
            TimelineRenderResponse response =
                await App.Services.ApiClient.QueueTimelineRenderAsync(
                    _loadedProjectId,
                    request,
                    _pageCancellation?.Token ?? CancellationToken.None);
            if (!response.Ok)
            {
                throw new InvalidOperationException("The backend did not accept the timeline render.");
            }

            StatusText.Text = $"Render {response.Job.Id}: {response.Job.Status}";
            ShowInfo(
                $"Timeline render queued as job {response.Job.Id}.",
                InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            StatusText.Text = "Render could not be queued.";
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private static (int Width, int Height) ResolveRenderDimensions(
        string mode,
        string aspect)
    {
        int longEdge = string.Equals(mode, "preview", StringComparison.OrdinalIgnoreCase)
            ? 1280
            : 1920;
        int shortEdge = string.Equals(mode, "preview", StringComparison.OrdinalIgnoreCase)
            ? 720
            : 1080;
        return aspect switch
        {
            "9:16" => (shortEdge, longEdge),
            "1:1" => (shortEdge, shortEdge),
            "4:5" => (
                string.Equals(mode, "preview", StringComparison.OrdinalIgnoreCase) ? 864 : 1080,
                string.Equals(mode, "preview", StringComparison.OrdinalIgnoreCase) ? 1080 : 1350),
            _ => (longEdge, shortEdge)
        };
    }

    private async Task RefreshRecoveryAsync()
    {
        if (string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            return;
        }

        try
        {
            JsonElement response = await App.Services.ApiClient.GetRecoveryAsync(
                _loadedProjectId,
                _pageCancellation?.Token ?? CancellationToken.None);
            _recoveryDocument = JsonNode.Parse(response.GetRawText()) as JsonObject;
            RefreshRecoverySummary();
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (Exception ex)
        {
            BackupSummaryText.Text = $"Recovery status unavailable: {ex.Message}";
        }
    }

    private void RefreshRecoverySummary()
    {
        bool needsRecovery = _recoveryDocument?["needs_recovery"]?.GetValue<bool>() == true;
        int candidateCount = (_recoveryDocument?["candidates"] as JsonArray)?.Count ?? 0;
        BackupSummaryText.Text = needsRecovery
            ? $"{candidateCount} recovery candidate{(candidateCount == 1 ? string.Empty : "s")} available."
            : candidateCount > 0
                ? $"{candidateCount} clean backup candidate{(candidateCount == 1 ? string.Empty : "s")} available."
                : "No recovery candidates are available.";
        RestoreBackupButton.IsEnabled = !_isBusy && needsRecovery && candidateCount > 0;
        ExportRecoveryButton.IsEnabled = !_isBusy && _recoveryDocument is not null;
        DeleteRecoveryButton.IsEnabled = !_isBusy && needsRecovery;
    }

    private async void RestoreBackup_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_loadedProjectId) ||
            !TryGetRecoveryCandidate(out string source, out string? snapshotName))
        {
            ShowInfo("No recovery candidate is available.", InfoBarSeverity.Warning);
            return;
        }

        if (!await ConfirmAsync(
            "Restore recovery data?",
            "The selected recovery candidate will replace the current project timeline.",
            "Restore"))
        {
            return;
        }

        SetBusy(true);
        try
        {
            await App.Services.ApiClient.ApplyRecoveryAsync(
                _loadedProjectId,
                new RecoveryApplyRequest(
                    source,
                    snapshotName,
                    StudioPageHelpers.ExpectedRevision(_project)),
                _pageCancellation?.Token ?? CancellationToken.None);
            await LoadActiveProjectAsync(forceReload: true);
            ShowInfo("Recovery data was restored.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private bool TryGetRecoveryCandidate(out string source, out string? snapshotName)
    {
        if (!TimelineRecovery.TrySelectCrashRecovery(
                _recoveryDocument,
                out TimelineRecoveryCandidate candidate))
        {
            source = "journal";
            snapshotName = null;
            return false;
        }

        source = candidate.Source;
        snapshotName = candidate.SnapshotName;
        return true;
    }

    private async void ExportRecovery_Click(object sender, RoutedEventArgs e)
    {
        if (_recoveryDocument is null || App.MainWindowInstance is null)
        {
            return;
        }

        try
        {
            var picker = new FileSavePicker
            {
                SuggestedStartLocation = PickerLocationId.DocumentsLibrary,
                SuggestedFileName = $"{_project?.Name ?? "timeline"}-recovery"
            };
            picker.FileTypeChoices.Add("JSON document", [".json"]);
            nint windowHandle = WinRT.Interop.WindowNative.GetWindowHandle(App.MainWindowInstance);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, windowHandle);
            StorageFile? file = await picker.PickSaveFileAsync();
            if (file is null)
            {
                return;
            }

            await FileIO.WriteTextAsync(
                file,
                _recoveryDocument.ToJsonString(new JsonSerializerOptions
                {
                    WriteIndented = true
                }));
            ShowInfo("Recovery metadata was exported.", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void DeleteRecovery_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_loadedProjectId) ||
            !await ConfirmAsync(
                "Discard recovery journal?",
                "This marks the autosave journal clean. Recovery snapshots and project files are not deleted.",
                "Discard"))
        {
            return;
        }

        SetBusy(true);
        try
        {
            await App.Services.ApiClient.DiscardRecoveryAsync(
                _loadedProjectId,
                _pageCancellation?.Token ?? CancellationToken.None);
            await RefreshRecoveryAsync();
            ShowInfo("The recovery journal was discarded.", InfoBarSeverity.Success);
        }
        catch (OperationCanceledException) when (_pageCancellation?.IsCancellationRequested == true)
        {
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            SetBusy(false);
        }
    }

    private async void ApplyRaw_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null)
        {
            return;
        }

        JsonObject? parsed;
        try
        {
            parsed = JsonNode.Parse(TimelineTextBox.Text) as JsonObject;
        }
        catch (JsonException ex)
        {
            ShowInfo($"Invalid JSON: {ex.Message}", InfoBarSeverity.Error);
            return;
        }

        if (parsed is null)
        {
            ShowInfo("Timeline JSON must be an object.", InfoBarSeverity.Warning);
            return;
        }

        try
        {
            _ = TimelineProjection.Project(parsed);
            _ = TimelineCameraProjection.Project(parsed);
            JsonObject before = CloneDocument(_timelineDocument);
            await CommitDocumentAsync(before, parsed, "timeline raw JSON applied");
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException)
        {
            ShowInfo(ex.Message, InfoBarSeverity.Error);
        }
    }

    private void RevertRaw_Click(object sender, RoutedEventArgs e)
    {
        if (_timelineDocument is null)
        {
            return;
        }

        TimelineTextBox.Text = _timelineDocument.ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true
        });
        PageInfoBar.IsOpen = false;
    }

    private void RefreshWorkflowPlanSummary()
    {
        if (string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            WorkspacePlanText.Text =
                "Select a project in Workspace to apply its plan on this Timeline.";
            return;
        }

        WorkspacePlanText.Text =
            $"Workspace plan variant {_loadedVariantIndex + 1} is selected for " +
            $"{_project?.Name ?? _loadedProjectId}. Append preserves current clips; overwrite replaces them.";
    }

    private async Task LoadWorkflowAssetsAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_loadedProjectId))
        {
            throw new InvalidOperationException("Select a project before refreshing sources.");
        }

        WorkspaceAssetsResponse response =
            await App.Services.ApiClient.GetProjectAssetsAsync(_loadedProjectId, cancellationToken);
        string[] paths = response.Assets.Audio
            .Concat(response.Assets.References)
            .Select(asset => asset.Path)
            .Where(path => !string.IsNullOrWhiteSpace(path))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        SourceAssetComboBox.ItemsSource = paths;

        if (paths.Contains(_selectedSourcePath, StringComparer.OrdinalIgnoreCase))
        {
            SourceAssetComboBox.SelectedItem = paths.First(
                path => string.Equals(path, _selectedSourcePath, StringComparison.OrdinalIgnoreCase));
        }
    }

    private void SelectSource(string sourcePath)
    {
        _selectedSourcePath = sourcePath;
        SelectedSourceText.Text = string.IsNullOrWhiteSpace(sourcePath)
            ? "No source selected."
            : sourcePath;
        UpdateCommandState();
    }

    private bool TryGetAutomationContext(bool requireSelection, out JsonObject timeline)
    {
        timeline = null!;
        if (_timelineDocument is null)
        {
            ShowAutomationInfo("Load a Timeline before changing its sources.", InfoBarSeverity.Warning);
            return false;
        }

        if (string.IsNullOrWhiteSpace(_selectedSourcePath))
        {
            ShowAutomationInfo("Select a project source or browse to a local file first.", InfoBarSeverity.Warning);
            return false;
        }

        if (requireSelection && string.IsNullOrWhiteSpace(_selectedLaneId))
        {
            ShowAutomationInfo("Select a Timeline clip before assigning its source.", InfoBarSeverity.Warning);
            return false;
        }

        timeline = CloneDocument(_timelineDocument);
        return true;
    }

    private async Task RefreshProjectRevisionAsync(
        string projectId,
        CancellationToken cancellationToken)
    {
        ProjectResponse refreshed = await App.Services.ApiClient.GetProjectAsync(projectId, cancellationToken);
        if (string.Equals(projectId, _loadedProjectId, StringComparison.Ordinal))
        {
            _project = refreshed.Project;
        }
    }

    private async Task HandleProjectRevisionConflictAsync(ProjectRevisionConflictException conflict)
    {
        _revisionConflictInterruptedOperation = true;
        if (!await StudioPageHelpers.ConfirmReloadAfterRevisionConflictAsync(XamlRoot, conflict))
        {
            ShowInfo(
                "The failed change was not applied. Your local Timeline edits remain open; reload the project before retrying.",
                InfoBarSeverity.Warning);
            return;
        }

        string? projectId = _loadedProjectId;
        await LoadActiveProjectAsync(forceReload: true);
        if (_project is not null &&
            string.Equals(projectId, _loadedProjectId, StringComparison.Ordinal))
        {
            ShowInfo(
                "The latest project revision is loaded. Review the Timeline, then retry your change.",
                InfoBarSeverity.Informational);
        }
    }

    private async Task RunAutomationAsync(
        string progressMessage,
        Func<CancellationToken, Task<string>> operation)
    {
        if (_isAutomationBusy)
        {
            return;
        }

        CancellationToken pageToken = _pageCancellation?.Token ?? CancellationToken.None;
        var cancellation = CancellationTokenSource.CreateLinkedTokenSource(pageToken);
        _automationCancellation = cancellation;
        _isAutomationBusy = true;
        _revisionConflictInterruptedOperation = false;
        AutomationProgressBar.IsIndeterminate = true;
        AutomationProgressBar.Visibility = Visibility.Visible;
        ShowAutomationInfo(progressMessage, InfoBarSeverity.Informational);
        UpdateCommandState();

        try
        {
            string result = await operation(cancellation.Token);
            if (!_revisionConflictInterruptedOperation)
            {
                ShowAutomationInfo(result, InfoBarSeverity.Success);
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested)
        {
            if (!pageToken.IsCancellationRequested)
            {
                ShowAutomationInfo("The Timeline workflow was canceled.", InfoBarSeverity.Warning);
            }
        }
        catch (ProjectRevisionConflictException conflict)
        {
            await HandleProjectRevisionConflictAsync(conflict);
        }
        catch (Exception ex)
        {
            ShowAutomationInfo(ex.Message, InfoBarSeverity.Error);
        }
        finally
        {
            if (ReferenceEquals(_automationCancellation, cancellation))
            {
                _automationCancellation = null;
            }

            cancellation.Dispose();
            _isAutomationBusy = false;
            AutomationProgressBar.IsIndeterminate = false;
            AutomationProgressBar.Visibility = Visibility.Collapsed;
            UpdateCommandState();
        }
    }

    private void ShowAutomationInfo(string message, InfoBarSeverity severity)
    {
        AutomationInfoBar.Title = severity switch
        {
            InfoBarSeverity.Success => "Workflow completed",
            InfoBarSeverity.Warning => "Workflow attention",
            InfoBarSeverity.Error => "Workflow failed",
            _ => "Workflow running",
        };
        AutomationInfoBar.Message = message;
        AutomationInfoBar.Severity = severity;
        AutomationInfoBar.IsOpen = true;
    }

    private void SetAutomationResult(JsonObject result)
    {
        AutomationResultTextBox.Text = result.ToJsonString(new JsonSerializerOptions
        {
            WriteIndented = true,
        });
    }

    private async Task NavigateWithSaveAsync(string destination)
    {
        if (_isAutomationBusy)
        {
            ShowAutomationInfo("Wait for or cancel the active Timeline workflow before navigating.", InfoBarSeverity.Warning);
            return;
        }

        if (_isDirty)
        {
            bool saved = false;
            await RunAutomationAsync(
                "Saving Timeline before handoff...",
                async token =>
                {
                    await SaveTimelineDocumentAsync(token);
                    saved = true;
                    return "Timeline saved for the next Studio workflow.";
                });
            if (!saved)
            {
                return;
            }
        }

        App.Navigate(destination);
    }

    private void UpdateCommandState()
    {
        bool hasTimeline = _timelineDocument is not null && !_isBusy;
        bool hasLaneSelection = SelectedLane is not null && !_isBusy;
        bool hasEditableLaneSelection = hasLaneSelection && !IsLaneLocked(SelectedLane);
        bool hasCameraSelection = SelectedCameraKeyframe is not null && !_isBusy;
        bool hasSelection = hasLaneSelection || hasCameraSelection;
        bool canRunAutomation = hasTimeline && !_isAutomationBusy;
        bool hasProject = !string.IsNullOrWhiteSpace(_loadedProjectId);
        bool hasSource = !string.IsNullOrWhiteSpace(_selectedSourcePath);
        UndoButton.IsEnabled = hasTimeline && _undoHistory.Count > 0;
        RedoButton.IsEnabled = hasTimeline && _redoHistory.Count > 0;
        SaveButton.IsEnabled = hasTimeline;
        PlayPauseButton.IsEnabled = hasTimeline;
        ApplyInspectorButton.IsEnabled = hasEditableLaneSelection;
        DuplicateClipButton.IsEnabled = hasCameraSelection || hasEditableLaneSelection;
        DeleteClipButton.IsEnabled = hasCameraSelection || hasEditableLaneSelection;
        MoveClipButton.IsEnabled = hasCameraSelection || hasEditableLaneSelection;
        QuantizeClipButton.IsEnabled =
            (hasCameraSelection || hasEditableLaneSelection) &&
            CanQuantizeToCurrentGrid();
        AddCameraButton.IsEnabled = hasTimeline;
        CameraKeyframeListView.IsEnabled = hasTimeline;
        SetCameraEditorEnabled(hasCameraSelection);
        RenderMasterButton.IsEnabled = hasTimeline;
        ApplyRawButton.IsEnabled = hasTimeline;
        RevertRawButton.IsEnabled = hasTimeline;
        RefreshWorkflowButton.IsEnabled = hasProject && !_isAutomationBusy;
        AppendPlanButton.IsEnabled = canRunAutomation && hasProject;
        OverwritePlanButton.IsEnabled = canRunAutomation && hasProject;
        GenerateAiEditButton.IsEnabled = canRunAutomation && hasProject;
        AppendAiEditButton.IsEnabled = canRunAutomation && hasProject && _hasAiEditProposal;
        ReplaceWithAiEditButton.IsEnabled = canRunAutomation && hasProject && _hasAiEditProposal;
        SourceAssetComboBox.IsEnabled = !_isAutomationBusy;
        BrowseSourceButton.IsEnabled = !_isAutomationBusy;
        AssignSourceButton.IsEnabled = canRunAutomation && hasEditableLaneSelection && hasSource;
        AddSourceClipButton.IsEnabled = canRunAutomation && hasSource;
        SequenceTrackButton.IsEnabled = canRunAutomation;
        ApplyMotionButton.IsEnabled = canRunAutomation && hasProject;
        CancelAutomationButton.IsEnabled = _isAutomationBusy;
        OpenWorkspaceButton.IsEnabled = !_isAutomationBusy;
        OpenRenderButton.IsEnabled = !_isAutomationBusy;
        OpenReviewButton.IsEnabled = !_isAutomationBusy;
        OpenOutputsButton.IsEnabled = !_isAutomationBusy;
        OpenQueueButton.IsEnabled = !_isAutomationBusy;
        OpenPlannerButton.IsEnabled = !_isAutomationBusy;
        OpenReactiveButton.IsEnabled = !_isAutomationBusy;
        UpdateSplitCommandState();
        RefreshRecoverySummary();
    }

    private void UpdateSplitCommandState()
    {
        if (SelectedLane is not TimelineLaneDocument lane ||
            _isBusy ||
            IsLaneLocked(lane))
        {
            SplitClipButton.IsEnabled = false;
            return;
        }

        SplitClipButton.IsEnabled = TimelineProjection.CanSplitAt(lane, _positionSeconds);
    }

    private void SetBusy(bool busy)
    {
        _isBusy = busy;
        UpdateCommandState();
    }

    private void ShowInfo(string message, InfoBarSeverity severity)
    {
        PageInfoBar.Message = message;
        PageInfoBar.Severity = severity;
        PageInfoBar.IsOpen = true;
    }

    private async Task<bool> ConfirmAsync(
        string title,
        string message,
        string primaryButtonText)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = XamlRoot,
            Title = title,
            Content = message,
            PrimaryButtonText = primaryButtonText,
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    private static bool TryReadFinite(NumberBox numberBox, out double value)
    {
        value = numberBox.Value;
        return double.IsFinite(value);
    }

    private static double ReadFiniteOrDefault(NumberBox numberBox, double fallback) =>
        double.IsFinite(numberBox.Value) ? numberBox.Value : fallback;

    private static string? GetSelectedTag(ComboBox comboBox) =>
        (comboBox.SelectedItem as ComboBoxItem)?.Tag?.ToString();

    private static void SelectComboByTag(ComboBox comboBox, string tag)
    {
        ComboBoxItem? match = comboBox.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(item => string.Equals(
                item.Tag?.ToString(),
                tag,
                StringComparison.OrdinalIgnoreCase));
        comboBox.SelectedItem = match ?? comboBox.Items.OfType<ComboBoxItem>().FirstOrDefault();
    }

    private static bool IsVisualLane(TimelineLaneDocument lane) =>
        lane.IsLayer
            ? IsVisualSourcePath(lane.SourcePath)
            : lane.Type.Contains("video", StringComparison.OrdinalIgnoreCase)
              || lane.Type.Contains("visual", StringComparison.OrdinalIgnoreCase)
              || lane.Type.Contains("image", StringComparison.OrdinalIgnoreCase)
              || IsVisualSourcePath(lane.SourcePath);

    private static bool IsVisualSourcePath(string? sourcePath) =>
        System.IO.Path.GetExtension(sourcePath ?? string.Empty).ToLowerInvariant() is
            ".avi" or ".bmp" or ".jpeg" or ".jpg" or ".m4v" or ".mkv" or ".mov" or
            ".mp4" or ".mpeg" or ".mpg" or ".png" or ".webm" or ".webp";

    private static string FormatClock(double seconds)
    {
        TimeSpan time = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return time.TotalHours >= 1
            ? $"{(int)time.TotalHours:00}:{time.Minutes:00}:{time.Seconds:00}"
            : $"{time.Minutes:00}:{time.Seconds:00}";
    }

    private static string FormatRulerTime(double seconds)
    {
        TimeSpan time = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return time.TotalHours >= 1
            ? $"{(int)time.TotalHours}:{time.Minutes:00}:{time.Seconds:00}"
            : $"{time.Minutes}:{time.Seconds:00}";
    }

    private static string FormatTimecode(double seconds)
    {
        double clamped = Math.Max(0, seconds);
        int totalFrames = (int)Math.Round(clamped * DefaultFps);
        int frames = totalFrames % (int)DefaultFps;
        int totalSeconds = totalFrames / (int)DefaultFps;
        int hours = totalSeconds / 3600;
        int minutes = (totalSeconds / 60) % 60;
        int remainingSeconds = totalSeconds % 60;
        return $"{hours:00}:{minutes:00}:{remainingSeconds:00}:{frames:00}";
    }

    private enum DragMode
    {
        None,
        Move,
        TrimStart,
        TrimEnd
    }

    private enum TimelinePointerTool
    {
        Select,
        Blade
    }
}

public sealed class CameraKeyframeListItem
{
    public string StableId { get; set; } = string.Empty;
    public string Summary { get; set; } = string.Empty;
    public string Detail { get; set; } = string.Empty;
}
