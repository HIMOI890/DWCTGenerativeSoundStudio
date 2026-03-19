from typing import Optional, Dict, Any
class CloudStorageManager:
    def __init__(self, provider: str="aws", config: Optional[dict]=None):
        self.provider = provider.lower()
        self.config = config or {}
    def upload_file(self, local_path: str, remote_path: str) -> Dict[str,Any]:
        return {"url": local_path}
