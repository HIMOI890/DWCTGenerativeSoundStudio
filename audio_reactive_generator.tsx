import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Square, Upload, Settings, Download, Mic, MicOff } from 'lucide-react';

const AudioReactiveGenerator = () => {
  // Audio processing state
  const [isRecording, setIsRecording] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [audioFile, setAudioFile] = useState(null);
  const [audioContext, setAudioContext] = useState(null);
  const [analyserNode, setAnalyserNode] = useState(null);
  const [audioData, setAudioData] = useState(new Uint8Array(1024));
  const [frequencyData, setFrequencyData] = useState(new Uint8Array(1024));
  
  // Generation parameters (reactive to audio)
  const [reactiveParams, setReactiveParams] = useState({
    zoom: 1.0,
    rotation_x: 0,
    rotation_y: 0,
    rotation_z: 0,
    translation_x: 0,
    translation_y: 0,
    translation_z: 0,
    cfg_scale: 7.0,
    strength: 0.75,
    brightness: 0.5,
    contrast: 1.0,
    energy_level: 0.0,
    bass_intensity: 0.0,
    mid_intensity: 0.0,
    treble_intensity: 0.0
  });
  
  // UI state
  const [sensitivity, setSensitivity] = useState(1.0);
  const [mappingPreset, setMappingPreset] = useState('cinematic');
  const [generationLog, setGenerationLog] = useState([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [totalFrames, setTotalFrames] = useState(240);
  
  // Refs
  const audioRef = useRef(null);
  const canvasRef = useRef(null);
  const animationRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  // Audio analysis function
  const analyzeAudio = useCallback((analyser, dataArray, freqArray) => {
    analyser.getByteTimeDomainData(dataArray);
    analyser.getByteFrequencyData(freqArray);
    
    // Calculate energy levels
    let sum = 0;
    let bassSum = 0;
    let midSum = 0;
    let trebleSum = 0;
    
    for (let i = 0; i < dataArray.length; i++) {
      const value = (dataArray[i] - 128) / 128;
      sum += value * value;
    }
    
    // Frequency analysis
    const bassRange = Math.floor(freqArray.length * 0.1);
    const midRange = Math.floor(freqArray.length * 0.5);
    const trebleRange = freqArray.length;
    
    for (let i = 0; i < bassRange; i++) {
      bassSum += freqArray[i];
    }
    for (let i = bassRange; i < midRange; i++) {
      midSum += freqArray[i];
    }
    for (let i = midRange; i < trebleRange; i++) {
      trebleSum += freqArray[i];
    }
    
    const energy = Math.sqrt(sum / dataArray.length);
    const bass = bassSum / (bassRange * 255);
    const mid = midSum / ((midRange - bassRange) * 255);
    const treble = trebleSum / ((trebleRange - midRange) * 255);
    
    return { energy, bass, mid, treble };
  }, []);
  
  // Map audio data to generation parameters
  const mapAudioToParams = useCallback((audioMetrics) => {
    const { energy, bass, mid, treble } = audioMetrics;
    const sens = sensitivity;
    
    const newParams = { ...reactiveParams };
    
    // Preset-based mappings
    if (mappingPreset === 'cinematic') {
      newParams.zoom = 1.0 + (energy * sens * 0.3);
      newParams.rotation_y = Math.sin(Date.now() * 0.001) * bass * sens * 45;
      newParams.translation_z = -energy * sens * 50;
      newParams.cfg_scale = 7.0 + (mid * sens * 3);
      newParams.strength = 0.65 + (treble * sens * 0.25);
    } else if (mappingPreset === 'psychedelic') {
      newParams.rotation_x = energy * sens * 90;
      newParams.rotation_y = bass * sens * 180;
      newParams.rotation_z = treble * sens * 45;
      newParams.zoom = 1.0 + Math.sin(energy * 10) * 0.5;
      newParams.brightness = 0.5 + mid * sens * 0.4;
      newParams.contrast = 1.0 + energy * sens * 0.8;
    } else if (mappingPreset === 'ambient') {
      newParams.translation_x = Math.sin(bass * 5) * sens * 20;
      newParams.translation_y = Math.cos(mid * 3) * sens * 15;
      newParams.zoom = 1.0 + energy * sens * 0.1;
      newParams.cfg_scale = 6.0 + treble * sens * 2;
    }
    
    // Store energy levels for visualization
    newParams.energy_level = energy;
    newParams.bass_intensity = bass;
    newParams.mid_intensity = mid;
    newParams.treble_intensity = treble;
    
    setReactiveParams(newParams);
  }, [reactiveParams, sensitivity, mappingPreset]);

  // Visualization canvas update
  const updateVisualization = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !analyserNode) return;
    
    const ctx = canvas.getContext('2d');
    const { width, height } = canvas;
    
    ctx.fillStyle = 'rgba(0, 0, 0, 0.1)';
    ctx.fillRect(0, 0, width, height);
    
    // Draw waveform
    ctx.strokeStyle = `hsl(${reactiveParams.energy_level * 360}, 70%, 60%)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    const sliceWidth = width / audioData.length;
    let x = 0;
    
    for (let i = 0; i < audioData.length; i++) {
      const v = (audioData[i] - 128) / 128;
      const y = (v * height) / 4 + height / 2;
      
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
      
      x += sliceWidth;
    }
    
    ctx.stroke();
    
    // Draw frequency bars
    const barWidth = width / frequencyData.length * 4;
    let barX = 0;
    
    for (let i = 0; i < frequencyData.length / 4; i++) {
      const barHeight = (frequencyData[i] / 255) * height * 0.8;
      
      const hue = (i / frequencyData.length) * 360;
      ctx.fillStyle = `hsla(${hue}, 70%, 60%, 0.6)`;
      ctx.fillRect(barX, height - barHeight, barWidth, barHeight);
      
      barX += barWidth;
    }
    
    // Draw reactive parameter indicators
    ctx.fillStyle = 'rgba(255, 255, 255, 0.8)';
    ctx.font = '12px monospace';
    ctx.fillText(`Energy: ${reactiveParams.energy_level.toFixed(3)}`, 10, 20);
    ctx.fillText(`Bass: ${reactiveParams.bass_intensity.toFixed(3)}`, 10, 35);
    ctx.fillText(`Mid: ${reactiveParams.mid_intensity.toFixed(3)}`, 10, 50);
    ctx.fillText(`Treble: ${reactiveParams.treble_intensity.toFixed(3)}`, 10, 65);
    ctx.fillText(`Zoom: ${reactiveParams.zoom.toFixed(2)}`, 10, 85);
    ctx.fillText(`CFG: ${reactiveParams.cfg_scale.toFixed(1)}`, 10, 100);
  }, [audioData, frequencyData, reactiveParams]);

  // Animation loop
  useEffect(() => {
    if (!isPlaying || !analyserNode) return;
    
    const animate = () => {
      if (!analyserNode) return;
      
      const dataArray = new Uint8Array(analyserNode.frequencyBinCount);
      const freqArray = new Uint8Array(analyserNode.frequencyBinCount);
      
      const metrics = analyzeAudio(analyserNode, dataArray, freqArray);
      setAudioData(dataArray);
      setFrequencyData(freqArray);
      
      mapAudioToParams(metrics);
      updateVisualization();
      
      if (isGenerating) {
        setCurrentFrame(prev => (prev + 1) % totalFrames);
      }
      
      animationRef.current = requestAnimationFrame(animate);
    };
    
    animate();
    
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [isPlaying, analyserNode, analyzeAudio, mapAudioToParams, updateVisualization, isGenerating, totalFrames]);

  // Start live microphone input
  const startMicInput = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        } 
      });
      
      const context = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = context.createAnalyser();
      const source = context.createMediaStreamSource(stream);
      
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.3;
      source.connect(analyser);
      
      setAudioContext(context);
      setAnalyserNode(analyser);
      setIsRecording(true);
      setIsPlaying(true);
      
      addToLog('Microphone input started');
    } catch (error) {
      addToLog(`Microphone error: ${error.message}`);
    }
  };

  // Stop microphone input
  const stopMicInput = () => {
    if (audioContext) {
      audioContext.close();
    }
    setIsRecording(false);
    setIsPlaying(false);
    addToLog('Microphone input stopped');
  };

  // Handle file upload
  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    
    try {
      const context = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = context.createAnalyser();
      const audioElement = new Audio();
      
      audioElement.src = URL.createObjectURL(file);
      const source = context.createMediaElementSource(audioElement);
      
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0.3;
      source.connect(analyser);
      source.connect(context.destination);
      
      setAudioFile(file);
      setAudioContext(context);
      setAnalyserNode(analyser);
      audioRef.current = audioElement;
      
      addToLog(`Audio file loaded: ${file.name}`);
    } catch (error) {
      addToLog(`File loading error: ${error.message}`);
    }
  };

  // Play/pause audio file
  const togglePlayback = () => {
    if (!audioRef.current) return;
    
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
    } else {
      audioRef.current.play();
      setIsPlaying(true);
    }
  };

  // Generate Deforum settings
  const generateSettings = async () => {
    setIsGenerating(true);
    addToLog('Generating reactive Deforum settings...');
    
    try {
      // Simulate generation process with current reactive parameters
      const settings = {
        animation_mode: "3D",
        max_frames: totalFrames,
        fps: 24,
        ...Object.fromEntries(
          Object.entries(reactiveParams)
            .filter(([key]) => !key.includes('_intensity') && key !== 'energy_level')
            .map(([key, value]) => [
              key === 'cfg_scale' ? 'scale' : key,
              `0:(${value})`
            ])
        ),
        prompts: {
          "0": `cinematic masterpiece, ${mappingPreset} style, highly detailed, dynamic lighting, audio reactive generation`
        },
        negative_prompts: {
          "0": "low quality, static, boring"
        }
      };
      
      // Create downloadable JSON
      const blob = new Blob([JSON.stringify(settings, null, 2)], { 
        type: 'application/json' 
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `reactive_deforum_${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      
      addToLog('Settings generated and downloaded');
    } catch (error) {
      addToLog(`Generation error: ${error.message}`);
    } finally {
      setIsGenerating(false);
    }
  };

  const addToLog = (message) => {
    setGenerationLog(prev => [...prev, `${new Date().toLocaleTimeString()}: ${message}`].slice(-10));
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-purple-900 via-blue-900 to-indigo-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        <div className="mb-8">
          <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
            Real-Time Audio Reactive Generation
          </h1>
          <p className="text-gray-300 mt-2">
            Generate dynamic Deforum parameters from live audio analysis
          </p>
        </div>
        
        {/* Audio Input Controls */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Mic className="w-5 h-5" />
              Live Input
            </h3>
            <div className="space-y-4">
              <button
                onClick={isRecording ? stopMicInput : startMicInput}
                className={`w-full py-3 px-4 rounded-lg font-medium transition-all ${
                  isRecording
                    ? 'bg-red-600 hover:bg-red-700'
                    : 'bg-green-600 hover:bg-green-700'
                }`}
              >
                {isRecording ? (
                  <>
                    <MicOff className="inline w-5 h-5 mr-2" />
                    Stop Recording
                  </>
                ) : (
                  <>
                    <Mic className="inline w-5 h-5 mr-2" />
                    Start Recording
                  </>
                )}
              </button>
            </div>
          </div>
          
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Upload className="w-5 h-5" />
              Audio File
            </h3>
            <div className="space-y-4">
              <label className="block">
                <input
                  type="file"
                  accept="audio/*"
                  onChange={handleFileUpload}
                  className="hidden"
                />
                <div className="w-full py-3 px-4 bg-blue-600 hover:bg-blue-700 rounded-lg text-center cursor-pointer transition-all">
                  Choose Audio File
                </div>
              </label>
              
              {audioFile && (
                <div className="flex gap-2">
                  <button
                    onClick={togglePlayback}
                    className="flex-1 py-2 px-4 bg-purple-600 hover:bg-purple-700 rounded-lg transition-all"
                  >
                    {isPlaying ? (
                      <Pause className="inline w-4 h-4 mr-2" />
                    ) : (
                      <Play className="inline w-4 h-4 mr-2" />
                    )}
                    {isPlaying ? 'Pause' : 'Play'}
                  </button>
                  <button
                    onClick={() => {
                      if (audioRef.current) {
                        audioRef.current.currentTime = 0;
                        audioRef.current.pause();
                        setIsPlaying(false);
                      }
                    }}
                    className="py-2 px-4 bg-gray-600 hover:bg-gray-700 rounded-lg transition-all"
                  >
                    <Square className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          </div>
          
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4 flex items-center gap-2">
              <Settings className="w-5 h-5" />
              Settings
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm mb-2">Sensitivity</label>
                <input
                  type="range"
                  min="0.1"
                  max="3.0"
                  step="0.1"
                  value={sensitivity}
                  onChange={(e) => setSensitivity(parseFloat(e.target.value))}
                  className="w-full"
                />
                <span className="text-xs text-gray-400">{sensitivity.toFixed(1)}</span>
              </div>
              
              <div>
                <label className="block text-sm mb-2">Mapping Preset</label>
                <select
                  value={mappingPreset}
                  onChange={(e) => setMappingPreset(e.target.value)}
                  className="w-full p-2 rounded bg-white/20 border border-white/30"
                >
                  <option value="cinematic">Cinematic</option>
                  <option value="psychedelic">Psychedelic</option>
                  <option value="ambient">Ambient</option>
                </select>
              </div>
            </div>
          </div>
        </div>
        
        {/* Visualization and Parameters */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4">Audio Visualization</h3>
            <canvas
              ref={canvasRef}
              width={600}
              height={300}
              className="w-full h-72 bg-black/50 rounded-lg"
            />
          </div>
          
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4">Reactive Parameters</h3>
            <div className="space-y-3 text-sm font-mono">
              <div className="grid grid-cols-2 gap-4">
                <div>Zoom: {reactiveParams.zoom.toFixed(3)}</div>
                <div>CFG Scale: {reactiveParams.cfg_scale.toFixed(2)}</div>
                <div>Rot X: {reactiveParams.rotation_x.toFixed(1)}°</div>
                <div>Rot Y: {reactiveParams.rotation_y.toFixed(1)}°</div>
                <div>Trans Z: {reactiveParams.translation_z.toFixed(1)}</div>
                <div>Strength: {reactiveParams.strength.toFixed(3)}</div>
              </div>
              
              <div className="pt-4 border-t border-white/20">
                <div className="grid grid-cols-4 gap-2 text-xs">
                  <div className="text-center">
                    <div className="text-gray-400">Energy</div>
                    <div className="h-20 bg-gradient-to-t from-blue-600 to-transparent rounded relative">
                      <div 
                        className="absolute bottom-0 left-0 right-0 bg-blue-400 rounded transition-all duration-75"
                        style={{ height: `${reactiveParams.energy_level * 100}%` }}
                      />
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-gray-400">Bass</div>
                    <div className="h-20 bg-gradient-to-t from-red-600 to-transparent rounded relative">
                      <div 
                        className="absolute bottom-0 left-0 right-0 bg-red-400 rounded transition-all duration-75"
                        style={{ height: `${reactiveParams.bass_intensity * 100}%` }}
                      />
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-gray-400">Mid</div>
                    <div className="h-20 bg-gradient-to-t from-green-600 to-transparent rounded relative">
                      <div 
                        className="absolute bottom-0 left-0 right-0 bg-green-400 rounded transition-all duration-75"
                        style={{ height: `${reactiveParams.mid_intensity * 100}%` }}
                      />
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-gray-400">Treble</div>
                    <div className="h-20 bg-gradient-to-t from-purple-600 to-transparent rounded relative">
                      <div 
                        className="absolute bottom-0 left-0 right-0 bg-purple-400 rounded transition-all duration-75"
                        style={{ height: `${reactiveParams.treble_intensity * 100}%` }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        {/* Generation Controls */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4">Generation Control</h3>
            <div className="space-y-4">
              <div className="flex gap-4">
                <div className="flex-1">
                  <label className="block text-sm mb-2">Total Frames</label>
                  <input
                    type="number"
                    value={totalFrames}
                    onChange={(e) => setTotalFrames(parseInt(e.target.value) || 240)}
                    className="w-full p-2 rounded bg-white/20 border border-white/30"
                    min="24"
                    max="10000"
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-sm mb-2">Current Frame</label>
                  <div className="p-2 bg-black/30 rounded font-mono">
                    {currentFrame} / {totalFrames}
                  </div>
                </div>
              </div>
              
              <button
                onClick={generateSettings}
                disabled={isGenerating || (!isRecording && !audioFile)}
                className={`w-full py-3 px-4 rounded-lg font-medium transition-all flex items-center justify-center gap-2 ${
                  isGenerating || (!isRecording && !audioFile)
                    ? 'bg-gray-600 cursor-not-allowed'
                    : 'bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700'
                }`}
              >
                <Download className="w-5 h-5" />
                {isGenerating ? 'Generating...' : 'Generate & Download Settings'}
              </button>
            </div>
          </div>
          
          <div className="bg-white/10 backdrop-blur-md rounded-xl p-6 border border-white/20">
            <h3 className="text-xl font-semibold mb-4">Generation Log</h3>
            <div className="bg-black/30 rounded-lg p-4 h-40 overflow-y-auto">
              <div className="space-y-1 text-sm font-mono">
                {generationLog.map((entry, index) => (
                  <div key={index} className="text-green-400">
                    {entry}
                  </div>
                ))}
                {generationLog.length === 0 && (
                  <div className="text-gray-500 text-center">
                    Log messages will appear here
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AudioReactiveGenerator;