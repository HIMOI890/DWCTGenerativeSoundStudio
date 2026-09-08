using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class TimelineVisibleRangeTests
{
    [TestMethod]
    public void LargeProject_CullsOffscreenEventsButRetainsSpanningClips()
    {
        var window = TimelineVisibleRange.Create(180000, 1500, 1200, 500, 100, 80);
        int count = Enumerable.Range(0, 10000).Count(i => window.Contains(i * 0.36, i * 0.36 + 0.3, i % 64));
        Assert.IsTrue(count < 50);
        Assert.IsTrue(window.Contains(0, 3600, 20));
        Assert.IsFalse(window.Contains(0, 3600, 0));
        Assert.IsFalse(window.Contains(0, 1, 20));
    }

    [TestMethod]
    public void InitialLayout_UsesBoundedFallbackAndOverscan()
    {
        var window = TimelineVisibleRange.Create(double.NaN, double.NaN, 0, 0, 100, 80);
        Assert.AreEqual(0d, window.StartSeconds);
        Assert.IsTrue(window.Contains(10, 10.5, 1));
        Assert.IsFalse(window.Contains(100, 101, 1));
    }
}
