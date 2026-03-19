import React, { useState, useRef } from 'react';
import { Upload, Play, Brain, Sparkles, Music, Eye, Heart, Zap, Download } from 'lucide-react';

const AIEnhancedMusicGenerator = () => {
  const [audioFile, setAudioFile] = useState(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [aiPrompts, setAiPrompts] = useState([]);
  const [generatingPrompts, setGeneratingPrompts] = useState(false);
  const [selectedModel, setSelectedModel] = useState('emotion-analysis');
  const fileInputRef = useRef(null);

  // Mock enhanced analysis function
  const performEnhancedAnalysis = async (file) => {
    setIsAnalyzing(true);
    
    // Simulate API call delay
    await new Promise(resolve => setTimeout(resolve, 2000));
    
    // Mock comprehensive analysis results
    const mockAnalysis = {
      basicInfo: {
        fileName: file.name,
        duration: "3:42",
        tempo: 128,
        key: "C major"
      },
      emotions: [
        { emotion: "joy", confidence: 0.85, intensity: "high" },
        { emotion: "nostalgia", confidence: 0.72, intensity: "medium" },
        { emotion: "hope", confidence: 0.68, intensity: "medium" },
        { emotion: "energy", confidence: 0.91, intensity: "very high" }
      ],
      visualImagery: [
        { element: "city lights", category: "urban", prominence: 0.89 },
        { element: "ocean", category: "nature", prominence: 0.74 },
        { element: "sunset", category: "lighting", prominence: 0.82 },
        { element: "dancing", category: "movement", prominence: 0.67 },
        { element: "golden hour", category: "lighting", prominence: 0.78 }
      ],
      themes: [
        { theme: "urban adventure", confidence: 0.88 },
        { theme: "personal growth", confidence: 0.73 },
        { theme: "connection", confidence: 0.69 }
      ],
      narrativeStructure: "journey_progression",
      sentimentProgression: [
        { segment: 1, sentiment: "contemplative", energy: 0.3 },
        { segment: 2, sentiment: "building", energy: 0.6 },
        { segment: 3, sentiment: "euphoric", energy: 0.95 },
        { segment: 4, sentiment: "triumphant", energy: 0.88 }
      ],
      spectralFeatures: {
        brightness: 0.74,
        warmth: 0.68,
        dynamicRange: 0.82
      }
    };
    
    setAnalysis(mockAnalysis);
    setIsAnalyzing(false);
  };

  // AI Prompt Generation using Claude API
  const generateAIPrompts = async () => {
    if (!analysis) return;
    
    setGeneratingPrompts(true);
    
    try {
      const context = buildAnalysisContext();
      const response = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model: "claude-sonnet-4-20250514",
          max_tokens: 1000,
          messages: [
            { 
              role: "user", 
              content: `Based on this comprehensive music analysis, generate 6 distinct cinematic prompts for AI video generation. Each should be 20-40 words and capture different moods/segments of the song.

Analysis Context:
${context}

Requirements:
- Use professional cinematography terms
- Include specific lighting and visual elements  
- Vary energy levels to match song progression
- Focus on visual storytelling

Generate only the prompts, one per line:` 
            }
          ]
        })
      });

      if (response.ok) {
        const data = await response.json();
        const promptText = data.content[0].text;
        const prompts = promptText.split('\n').filter(line => line.trim()).map((prompt, index) => ({
          id: index + 1,
          text: prompt.trim(),
          segment: Math.floor(index / 2) + 1,
          energy: analysis.sentimentProgression[Math.min(index, analysis.sentimentProgression.length - 1)]?.energy || 0.5
        }));
        
        setAiPrompts(prompts);
      } else {
        // Fallback to template-based prompts
        generateFallbackPrompts();
      }
    } catch (error) {
      console.error('AI prompt generation failed:', error);
      generateFallbackPrompts();
    }
    
    setGeneratingPrompts(false);
  };

  const generateFallbackPrompts = () => {
    const fallbackPrompts = [
      {
        id: 1,
        text: "Cinematic urban landscape at golden hour, dynamic camera movement through neon-lit streets, vibrant colors, high energy",
        segment: 1,
        energy: 0.8
      },
      {
        id: 2,
        text: "Dreamy ocean waves at sunset, soft lighting, contemplative mood, gentle movement, warm color palette",
        segment: 2,
        energy: 0.4
      },
      {
        id: 3,
        text: "Euphoric dancing silhouettes against city lights, explosive energy, dramatic lighting, dynamic composition",
        segment: 3,
        energy: 0.95
      },
      {
        id: 4,
        text: "Triumphant aerial view of cityscape, soaring camera movement, brilliant sunshine, inspiring atmosphere",
        segment: 4,
        energy: 0.85
      }
    ];
    setAiPrompts(fallbackPrompts);
  };

  const buildAnalysisContext = () => {
    if (!analysis) return "";
    
    return `
Song: ${analysis.basicInfo.fileName}
Duration: ${analysis.basicInfo.duration}
Tempo: ${analysis.basicInfo.tempo} BPM
Key: ${analysis.basicInfo.key}

Primary Emotions: ${analysis.emotions.map(e => `${e.emotion} (${(e.confidence * 100).toFixed(0)}%)`).join(', ')}

Visual Elements: ${analysis.visualImagery.map(v => v.element).join(', ')}

Themes: ${analysis.themes.map(t => t.theme).join(', ')}

Narrative Structure: ${analysis.narrativeStructure}

Energy Progression:
${analysis.sentimentProgression.map(s => `Segment ${s.segment}: ${s.sentiment} (energy: ${(s.energy * 100).toFixed(0)}%)`).join('\n')}

Spectral Features:
- Brightness: ${(analysis.spectralFeatures.brightness * 100).toFixed(0)}%
- Warmth: ${(analysis.spectralFeatures.warmth * 100).toFixed(0)}%
- Dynamic Range: ${(analysis.spectralFeatures.dynamicRange * 100).toFixed(0)}%
    `.trim();
  };

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (file && file.type.startsWith('audio/')) {
      setAudioFile(file);
      setAnalysis(null);
      setAiPrompts([]);
    }
  };

  const exportResults = () => {
    const exportData = {
      analysis,
      aiPrompts,
      generatedAt: new Date().toISOString(),
      settings: {
        model: selectedModel,
        fileName: audioFile?.name
      }
    };
    
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `music-analysis-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-8">
      <div className="text-center space-y-4">
        <h1 className="text-4xl font-bold text-gray-900 flex items-center justify-center gap-3">
          <Music className="text-blue-600" />
          AI-Enhanced Music Video Generator
          <Sparkles className="text-yellow-500" />
        </h1>
        <p className="text-lg text-gray-600 max-w-2xl mx-auto">
          Upload music to get comprehensive AI analysis including emotions, visual imagery, themes, and AI-generated cinematic prompts.
        </p>
      </div>

      {/* File Upload */}
      <div className="bg-white rounded-lg shadow-lg p-8">
        <h2 className="text-2xl font-semibold mb-4 flex items-center gap-2">
          <Upload className="text-blue-600" />
          Upload Audio File
        </h2>
        
        <div 
          className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-blue-400 transition-colors cursor-pointer"
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            onChange={handleFileUpload}
            className="hidden"
          />
          
          {audioFile ? (
            <div className="space-y-2">
              <Play className="mx-auto text-green-600" size={48} />
              <p className="font-medium text-gray-900">{audioFile.name}</p>
              <p className="text-sm text-gray-500">Ready for analysis</p>
            </div>
          ) : (
            <div className="space-y-2">
              <Upload className="mx-auto text-gray-400" size={48} />
              <p className="text-gray-600">Click to upload audio file</p>
              <p className="text-sm text-gray-500">MP3, WAV, M4A supported</p>
            </div>
          )}
        </div>

        {audioFile && (
          <div className="mt-6 flex gap-4">
            <div className="flex-1">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Analysis Model
              </label>
              <select 
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="emotion-analysis">Advanced Emotion Analysis</option>
                <option value="theme-extraction">Theme & Narrative Extraction</option>
                <option value="comprehensive">Comprehensive Analysis</option>
              </select>
            </div>
            
            <div className="flex items-end">
              <button
                onClick={() => performEnhancedAnalysis(audioFile)}
                disabled={isAnalyzing}
                className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {isAnalyzing ? (
                  <>
                    <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Brain className="w-4 h-4" />
                    Analyze
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Analysis Results */}
      {analysis && (
        <div className="space-y-6">
          {/* Basic Info */}
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Music className="text-purple-600" />
              Audio Analysis
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <p className="text-2xl font-bold text-blue-600">{analysis.basicInfo.duration}</p>
                <p className="text-sm text-gray-600">Duration</p>
              </div>
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <p className="text-2xl font-bold text-green-600">{analysis.basicInfo.tempo}</p>
                <p className="text-sm text-gray-600">BPM</p>
              </div>
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <p className="text-2xl font-bold text-purple-600">{analysis.basicInfo.key}</p>
                <p className="text-sm text-gray-600">Key</p>
              </div>
              <div className="text-center p-4 bg-gray-50 rounded-lg">
                <p className="text-2xl font-bold text-orange-600">{analysis.emotions.length}</p>
                <p className="text-sm text-gray-600">Emotions</p>
              </div>
            </div>
          </div>

          {/* Emotions */}
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Heart className="text-red-500" />
              Detected Emotions
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {analysis.emotions.map((emotion, index) => (
                <div key={index} className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                  <div>
                    <p className="font-medium capitalize">{emotion.emotion}</p>
                    <p className="text-sm text-gray-600">Intensity: {emotion.intensity}</p>
                  </div>
                  <div className="text-right">
                    <div className="w-24 bg-gray-200 rounded-full h-2">
                      <div 
                        className="bg-blue-600 h-2 rounded-full" 
                        style={{ width: `${emotion.confidence * 100}%` }}
                      />
                    </div>
                    <p className="text-xs text-gray-500 mt-1">{Math.round(emotion.confidence * 100)}%</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Visual Imagery */}
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Eye className="text-blue-500" />
              Visual Imagery
            </h3>
            <div className="flex flex-wrap gap-3">
              {analysis.visualImagery.map((item, index) => (
                <div key={index} className="flex items-center gap-2 px-4 py-2 bg-blue-50 rounded-full">
                  <span className="font-medium">{item.element}</span>
                  <span className="text-xs bg-blue-200 px-2 py-1 rounded-full">{Math.round(item.prominence * 100)}%</span>
                </div>
              ))}
            </div>
          </div>

          {/* Energy Progression */}
          <div className="bg-white rounded-lg shadow-lg p-6">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Zap className="text-yellow-500" />
              Energy Progression
            </h3>
            <div className="space-y-3">
              {analysis.sentimentProgression.map((segment, index) => (
                <div key={index} className="flex items-center gap-4">
                  <div className="w-16 text-sm font-medium">Seg {segment.segment}</div>
                  <div className="flex-1">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm capitalize">{segment.sentiment}</span>
                      <span className="text-xs text-gray-500">{Math.round(segment.energy * 100)}%</span>
                    </div>
                    <div className="w-full bg-gray-200 rounded-full h-2">
                      <div 
                        className={`h-2 rounded-full transition-all ${
                          segment.energy > 0.7 ? 'bg-red-500' :
                          segment.energy > 0.4 ? 'bg-yellow-500' : 'bg-green-500'
                        }`}
                        style={{ width: `${segment.energy * 100}%` }}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* AI Prompt Generation */}
          <div className="bg-white rounded-lg shadow-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xl font-semibold flex items-center gap-2">
                <Sparkles className="text-purple-500" />
                AI-Generated Prompts
              </h3>
              <button
                onClick={generateAIPrompts}
                disabled={generatingPrompts}
                className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {generatingPrompts ? (
                  <>
                    <div className="animate-spin w-4 h-4 border-2 border-white border-t-transparent rounded-full" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Brain className="w-4 h-4" />
                    Generate AI Prompts
                  </>
                )}
              </button>
            </div>

            {aiPrompts.length > 0 && (
              <div className="space-y-4">
                {aiPrompts.map((prompt) => (
                  <div key={prompt.id} className="p-4 border border-gray-200 rounded-lg">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-purple-600">
                        Segment {prompt.segment}
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-500">Energy:</span>
                        <div className="w-12 bg-gray-200 rounded-full h-2">
                          <div 
                            className="bg-purple-600 h-2 rounded-full" 
                            style={{ width: `${prompt.energy * 100}%` }}
                          />
                        </div>
                      </div>
                    </div>
                    <p className="text-gray-800">{prompt.text}</p>
                  </div>
                ))}
                
                <div className="flex justify-end pt-4">
                  <button
                    onClick={exportResults}
                    className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center gap-2"
                  >
                    <Download className="w-4 h-4" />
                    Export Results
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default AIEnhancedMusicGenerator;