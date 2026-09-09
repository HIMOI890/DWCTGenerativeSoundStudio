using System.Text.Json;
using EdmgStudio.Core.Models;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class ReactiveKeyframeEditorTests
{
    [TestMethod]
    public void RefinementPreservesSourceTimingExtensionsAndOriginalDocument()
    {
        using var source = JsonDocument.Parse("""{"id":"key-a","source_id":"scene-a","t":1.2,"frame":36,"sample":"57600","strength":0.4,"zoom":1.01,"extension":{"keep":true}}""");
        var editor = new ReactiveKeyframeEditor(source.RootElement);
        Assert.IsTrue(editor.Refine(0.7, 1.3));
        var result = editor.ToJson();
        Assert.AreEqual("57600", result.GetProperty("sample").GetString());
        Assert.AreEqual(36, result.GetProperty("frame").GetInt32());
        Assert.AreEqual(1.2, result.GetProperty("t").GetDouble());
        Assert.AreEqual("scene-a", result.GetProperty("source_id").GetString());
        Assert.IsTrue(result.GetProperty("extension").GetProperty("keep").GetBoolean());
        Assert.AreEqual(0.4, source.RootElement.GetProperty("strength").GetDouble());
        Assert.AreEqual(0.7, result.GetProperty("strength").GetDouble());
    }

    [TestMethod]
    public void LockedKeyframeRejectsRefinement()
    {
        using var source = JsonDocument.Parse("""{"id":"locked","locked":true,"strength":0.4,"zoom":1}""");
        var editor = new ReactiveKeyframeEditor(source.RootElement);
        Assert.IsFalse(editor.IsEditable);
        Assert.Throws<InvalidOperationException>(() => editor.Refine(0.5, 1.2));
    }
}
