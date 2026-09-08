namespace EdmgStudio.Core.Models;

/// <summary>Projects ruler marks into the visible time window without changing project timing.</summary>
public static class TimelineViewport
{
    public static IEnumerable<double> RulerTicks(double durationSeconds, double pixelsPerSecond,
        double horizontalOffset, double viewportWidth, double stepSeconds)
    {
        if (!double.IsFinite(durationSeconds) || durationSeconds < 0
            || !double.IsFinite(pixelsPerSecond) || pixelsPerSecond <= 0
            || !double.IsFinite(stepSeconds) || stepSeconds <= 0)
            yield break;

        double offset = double.IsFinite(horizontalOffset) ? Math.Max(0, horizontalOffset) : 0;
        double width = double.IsFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 900;
        double first = Math.Max(0, Math.Floor(offset / pixelsPerSecond / stepSeconds) - 1);
        double last = Math.Min(Math.Ceiling(durationSeconds / stepSeconds),
            Math.Ceiling((offset + width) / pixelsPerSecond / stepSeconds) + 1);
        for (double index = first; index <= last; index++)
            yield return Math.Min(durationSeconds, index * stepSeconds);
    }
}
