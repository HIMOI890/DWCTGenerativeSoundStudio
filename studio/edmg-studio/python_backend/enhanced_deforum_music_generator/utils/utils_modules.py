# utils/logging_utils.py
"""
Logging Utilities
Centralized logging configuration
"""

import logging
import sys
from pathlib import Path

def setup_logger(level: str = "INFO", log_file: str = None) -> logging.Logger:
    """Setup centralized logging"""
    
    # Create logger
    logger = logging.getLogger("deforum_music")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Failed to setup file logging: {e}")
    
    return logger

# utils/package_creator.py
"""
Package Creator
Creates output packages with settings and documentation
"""

import os
import json
import tempfile
import zipfile
import time
from pathlib import Path

class PackageCreator:
    """Creates comprehensive output packages"""
    
    def __init__(self, config):
        self.config = config
    
    def create(self, settings, audio_analysis, lyric_analysis, audio_file):
        """Create complete output package"""
        
        tmpdir = tempfile.mkdtemp()
        
        try:
            # Main settings file
            settings_file = Path(tmpdir) / "enhanced_deforum_settings.json"
            with open(settings_file, "w") as f:
                json.dump(settings, f, indent=2)
            
            # Analysis report
            analysis_file = Path(tmpdir) / "analysis_report.md"
            with open(analysis_file, "w") as f:
                f.write(self._create_analysis_report(
                    settings, audio_analysis, lyric_analysis, audio_file
                ))
            
            # Quick start guide
            quickstart_file = Path(tmpdir) / "QUICKSTART.md"
            with open(quickstart_file, "w") as f:
                f.write(self._create_quickstart_guide(settings, audio_file))
            
            # Configuration summary
            config_file = Path(tmpdir) / "configuration_summary.json"
            with open(config_file, "w") as f:
                json.dump(self.config.get_summary(), f, indent=2)
            
            # Create ZIP package
            zip_path = Path(tmpdir) / "Enhanced_Deforum_Package.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(settings_file, "enhanced_deforum_settings.json")
                zipf.write(analysis_file, "analysis_report.md")
                zipf.write(quickstart_file, "QUICKSTART.md")
                zipf.write(config_file, "configuration_summary.json")
            
            return str(zip_path)
            
        except Exception as e:
            print(f"Package creation failed: {e}")
            return None
    
    def _create_analysis_report(self, settings, audio_analysis, lyric_analysis, audio_file):
        """Create detailed analysis report"""
        
        return f"""# Enhanced Deforum Analysis Report

## File Information
- **Audio File**: {os.path.basename(audio_file)}
- **Duration**: {audio_analysis.duration:.1f} seconds
- **Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}

## Audio Analysis
- **Tempo**: {audio_analysis.tempo_bpm:.1f} BPM (confidence: {audio_analysis.tempo_confidence*100:.0f}%)
- **Rhythm Pattern**: {audio_analysis.rhythm_pattern.title()}
- **Dynamic Range**: {audio_analysis.dynamic_range:.3f}
- **Energy Segments**: {len(audio_analysis.energy_segments)} analyzed
- **Reactive Points**: {len(audio_analysis.reactive_points)} generated
- **Spectral Features**: Brightness {audio_analysis.spectral_features.get('brightness', 0):.2f}, Warmth {audio_analysis.spectral_features.get('warmth', 0):.2f}

## Lyric Analysis
- **Has Lyrics**: {'Yes' if lyric_analysis.has_lyrics else 'No'}
- **Emotions Detected**: {', '.join(lyric_analysis.emotions) if lyric_analysis.emotions else 'None'}
- **Emotional Intensity**: {lyric_analysis.emotional_intensity*100:.0f}%
- **Themes Found**: {len(lyric_analysis.themes)}
- **Visual Elements**: {len(lyric_analysis.visual_elements)} mapped

## Generated Settings
- **Total Frames**: {settings.get('max_frames', 0):,}
- **Resolution**: {settings.get('W', 1024)}x{settings.get('H', 576)}
- **FPS**: {settings.get('fps', 24)}
- **Animation Mode**: {settings.get('animation_mode', '3D')}
- **Audio Reactive**: Yes

This analysis used enhanced algorithms for superior audio-visual synchronization.
"""
    
    def _create_quickstart_guide(self, settings, audio_file):
        """Create user-friendly quickstart guide"""
        
        return f"""# Enhanced Deforum Quickstart Guide

## Quick Import (30 seconds)
1. Open Automatic1111 WebUI
2. Navigate to txt2img tab
3. Scroll down to Scripts → Select "Deforum"
4. Click "Load Settings" → Select `enhanced_deforum_settings.json`
5. In Video settings → Set audio path to: `{os.path.basename(audio_file)}`
6. Click Generate!

## Settings Overview
- **Frames**: {settings.get('max_frames', 0):,} total
- **FPS**: {settings.get('fps', 24)}
- **Resolution**: {settings.get('W', 1024)}×{settings.get('H', 576)}
- **Animation**: 3D with depth warping
- **Audio Reactive**: Zoom, strength, and camera movement

## Enhanced Features Applied
- Memory-efficient processing
- Multi-algorithm beat detection
- Smooth easing interpolation
- Intelligent prompt generation
- Anti-jitter protection
- Advanced spectral mapping

## Troubleshooting
- **Out of Memory**: Settings are optimized for efficiency
- **Slow Processing**: Generated settings balance quality and speed
- **Beat Sync Issues**: Multiple algorithms provide robust fallbacks
- **Quality Concerns**: Enhanced coherence settings prevent artifacts

Generated with Enhanced Deforum Music Generator (Refactored Architecture)
"""

# utils/report_generator.py
"""
Report Generator
Creates user-friendly status reports
"""

class ReportGenerator:
    """Generates comprehensive status reports"""
    
    def generate(self, audio_analysis, lyric_analysis, processing_time):
        """Generate user-friendly status report"""
        
        return f"""Enhanced A1111/Deforum Settings Generated Successfully!

Audio Analysis Complete
• Duration: {audio_analysis.duration:.1f}s ({audio_analysis.duration/60:.1f} minutes)
• Tempo: {audio_analysis.tempo_bpm:.1f} BPM (confidence: {audio_analysis.tempo_confidence*100:.0f}%)
• Pattern: {audio_analysis.rhythm_pattern.title()} rhythm
• Energy Range: {min(audio_analysis.energy_segments):.2f} - {max(audio_analysis.energy_segments):.2f}
• Reactive Points: {len(audio_analysis.reactive_points)} intelligent keyframes
• Spectral Analysis: Brightness {audio_analysis.spectral_features.get('brightness', 0):.2f}, Warmth {audio_analysis.spectral_features.get('warmth', 0):.2f}

Lyric Analysis {'Complete' if lyric_analysis.has_lyrics else 'Skipped'}
• Lyrics Found: {'Yes' if lyric_analysis.has_lyrics else 'No (instrumental or failed transcription)'}
• Emotions: {', '.join(lyric_analysis.emotions[:3]) if lyric_analysis.emotions else 'None detected'}
• Emotional Intensity: {lyric_analysis.emotional_intensity*100:.0f}%
• Visual Elements: {len(lyric_analysis.visual_elements)} mapped to animation
• Themes: {len(lyric_analysis.themes)} key concepts identified

Enhanced Features Applied
• Memory-efficient chunked processing for optimal performance
• Multi-algorithm beat detection with confidence scoring
• Smooth cubic/sine easing functions for natural motion
• Intelligent prompt generation from lyrical content
• Anti-jitter keyframe optimization prevents artifacts
• Advanced spectral-to-visual color mapping

Performance Metrics
• Total Processing Time: {processing_time:.1f} seconds
• Memory Usage: Optimized through chunked analysis
• Analysis Quality: Enhanced algorithms with fallback protection

Package Contents
• enhanced_deforum_settings.json - Ready for A1111 import
• analysis_report.md - Comprehensive technical details
• QUICKSTART.md - Step-by-step setup instructions
• configuration_summary.json - Current system configuration

Ready for direct import into A1111 WebUI with enhanced quality and performance!
The refactored architecture provides better maintainability and cleaner separation of concerns."""