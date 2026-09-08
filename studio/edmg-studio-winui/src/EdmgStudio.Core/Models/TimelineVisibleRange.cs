namespace EdmgStudio.Core.Models;

/// <summary>Presentation-only culling; project events remain in the shared model.</summary>
public readonly record struct TimelineVisibleRange(double StartSeconds, double EndSeconds, int FirstTrack, int LastTrack)
{
    public static TimelineVisibleRange Create(double horizontalOffset, double verticalOffset,
        double width, double height, double pixelsPerSecond, double trackHeight)
    {
        if (!double.IsFinite(pixelsPerSecond) || pixelsPerSecond <= 0 ||
            !double.IsFinite(trackHeight) || trackHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(pixelsPerSecond));
        horizontalOffset = double.IsFinite(horizontalOffset) ? Math.Max(0, horizontalOffset) : 0;
        verticalOffset = double.IsFinite(verticalOffset) ? Math.Max(0, verticalOffset) : 0;
        width = double.IsFinite(width) && width > 0 ? width : 900;
        height = double.IsFinite(height) && height > 0 ? height : 600;
        return new(Math.Max(0, horizontalOffset - 160) / pixelsPerSecond,
            (horizontalOffset + width + 160) / pixelsPerSecond,
            Math.Max(0, (int)Math.Floor(verticalOffset / trackHeight) - 1),
            (int)Math.Ceiling((verticalOffset + height) / trackHeight) + 1);
    }

    public bool Contains(double start, double end, int track) =>
        track >= FirstTrack && track <= LastTrack && end >= StartSeconds && start <= EndSeconds;
}
