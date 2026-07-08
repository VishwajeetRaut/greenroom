import os
import tempfile
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse

from auth import AuthenticatedUser, get_current_user
from services import tts
from services.rate_limit import check_rate_limit

router = APIRouter(prefix="/tts", tags=["tts"])


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


@router.get("/speak")
async def speak(
    background_tasks: BackgroundTasks,
    text: str = Query(..., min_length=1, max_length=2000),
    user: AuthenticatedUser = Depends(get_current_user),
):
    check_rate_limit(user.id)
    out_path = os.path.join(tempfile.gettempdir(), f"greenroom-tts-{uuid.uuid4().hex}.mp3")
    await tts.synthesize_to_file(text, out_path)
    background_tasks.add_task(_cleanup, out_path)
    return FileResponse(out_path, media_type="audio/mpeg", filename="speech.mp3")
