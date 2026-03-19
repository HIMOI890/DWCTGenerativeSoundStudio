from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title='Enhanced Deforum Music Generator API', version='0.1.0')

@app.get('/health/')
def health():
    return {'status': 'ok'}

@app.get('/status/')
def status():
    return {
        'service': 'enhanced-deforum-music-generator',
        'time_utc': datetime.now(timezone.utc).isoformat(),
    }
