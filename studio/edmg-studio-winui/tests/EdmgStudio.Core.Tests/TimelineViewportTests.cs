using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class TimelineViewportTests
{
    [TestMethod]
    [DataRow(60d)]
    [DataRow(600d)]
    [DataRow(86400d)]
    public void RulerWorkIsBoundedByViewportRatherThanProjectDuration(double duration)
    {
        double[] ticks = TimelineViewport.RulerTicks(duration, 360, 0, 1920, 0.25).ToArray();
        Assert.IsTrue(ticks.Length <= 25);
        Assert.AreEqual(0d, ticks[0]);
        Assert.IsTrue(ticks[^1] * 360 >= 1920);
    }

    [TestMethod]
    public void ScrolledRulerCoversViewportAndPreservesFractionalProjectEnd()
    {
        double[] ticks = TimelineViewport.RulerTicks(600.125, 360, 598 * 360, 1920, 0.25).ToArray();
        Assert.AreEqual(597.75, ticks[0]);
        Assert.AreEqual(600.125, ticks[^1]);
        Assert.AreEqual(ticks.Length, ticks.Distinct().Count());
        Assert.IsTrue(ticks.All(time => time <= 600.125));
    }

    [TestMethod]
    public void InvalidTransportDoesNotCreateVisuals()
    {
        Assert.IsEmpty(TimelineViewport.RulerTicks(double.NaN, 80, 0, 900, 1));
        Assert.IsEmpty(TimelineViewport.RulerTicks(60, 0, 0, 900, 1));
        Assert.IsEmpty(TimelineViewport.RulerTicks(60, 80, 0, 900, 0));
    }
}
