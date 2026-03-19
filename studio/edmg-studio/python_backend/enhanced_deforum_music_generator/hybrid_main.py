import multiprocessing
import uvicorn
from pathlib import Path

from enhanced_deforum_music_generator.config.config_system import Config
from enhanced_deforum_music_generator.__main__ import main as gradio_main
from enhanced_deforum_music_generator.api.main import app as fastapi_app

# Load config once and share
config = Config.from_file(Path("custom.yaml"))

def run_gradio():
    gradio_main(config=config)

def run_fastapi():
    # Attach config to app state for API routes
    fastapi_app.state.config = config
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    p1 = multiprocessing.Process(target=run_gradio)
    p2 = multiprocessing.Process(target=run_fastapi)

    p1.start()
    p2.start()

    p1.join()
    p2.join()
