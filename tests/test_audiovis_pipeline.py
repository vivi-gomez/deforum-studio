from deforum import DeforumAnimationPipeline
import subprocess
import threading
import os
import time
import math
import requests
import shutil
from subprocess import Popen
import tempfile
import mutagen
from deforum.pipeline_utils import load_settings
from deforum.utils.constants import config
from deforum.utils.logging_config import logger

#############
# Setup
INPUT_AUDIO = "https://vizrecord.app/audio/120bpm.mp3"
MILKDROP_PRESET = os.path.join(config.presets_path, 'projectm', 'waveform.milk')
BASE_DEFORUM_PRESET = os.path.join(config.presets_path, 'settings', 'Classic-3D-Motion.txt')
FPS = 24
WIDTH = 1024
HEIGHT = 576
OVERRIDE_FRAME_COUNT = 48 # limit frame count for testing, set to None to generate full length
#############

def run_projectm(input_audio: str, host_output_path : str, preset : str, fps: int = 20, width: int = 1024, height: int = 576) -> Popen[bytes]:

    logger.info(f"Starting projectM. Writing frames to: {host_output_path}")

    assert os.path.exists(input_audio) and os.path.isfile(input_audio)
    assert os.path.exists(host_output_path) and os.path.isdir(host_output_path)

    # Update with your path to 'texture' subdirectory of https://github.com/projectM-visualizer/presets-milkdrop-texture-pack
    texture_path = "/home/rewbs/milkdrop/textures"
    projectm_path = config.projectm_executable

    if not shutil.which(projectm_path):
        logger.error("No projectm executable found. Tried: " + projectm_path)
        return
    if not os.path.exists(preset):
        logger.error("No projectM preset found at: " + preset)
        return
    if not os.path.exists(texture_path):
        # Not fatal but may affect output of some presets that depend on textures.
        logger.warning("No projectM texture directory found. Some presets may not render as expected. Tried: " + texture_path)

    command = [
        projectm_path,
        "--outputPath", f"{host_output_path}",
        "--outputType", "image",
        "--texturePath", f"{texture_path}",
        "--width", f"{width}",
        "--height", f"{height}",
        "--beatSensitivity", "2.0",
        "--calibrate", "1",
        "--fps", f"{fps}",
        "--presetFile",  preset,
        "--audioPath", f"{input_audio}"
    ]

    # Start the process (without blocking)
    logger.info("Running projectM with command: " + " ".join(command))
    projectm_env = os.environ.copy()
    projectm_env["EGL_PLATFORM"] = "surfaceless" 
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=projectm_env)

    return process

def monitor_projectm(process : Popen[bytes]):
    while True:
        logger.info("ProjectM running...")        
        output = process.stdout.readline()
        error = process.stderr.readline()        
        if output:
            logger.info("[PROJECTM - stdout] - " +  output.decode().strip())
        if error:
            logger.error("[PROJECTM - stderr] - " +  error.decode().strip())

        if process.poll() is not None:
            output = process.stdout.read()
            error = process.stderr.read()
            if output:
                logger.info("[PROJECTM - stdout] - " +  output.decode().strip())
            if error:
                logger.error("[PROJECTM - stderr] - " +  error.decode().strip())
            if (process.returncode != 0):
                logger.error(f"ProjectM exited with code {process.returncode}")
            else:
                logger.info("ProjectM completed successfully")
            break    

        time.sleep(1) 


def get_audio_duration(audio_file):
    audio = mutagen.File(audio_file)
    return audio.info.length

if __name__ == "__main__":

    audio_file_path = None
    if INPUT_AUDIO.startswith("http"):
        requests.get(INPUT_AUDIO)
        audio_file_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        with open(audio_file_path, "wb") as file:
            file.write(requests.get(INPUT_AUDIO).content)
    else:
        audio_file_path = INPUT_AUDIO
            
    expected_frame_count = OVERRIDE_FRAME_COUNT or math.floor(FPS * get_audio_duration(audio_file_path))

    job_name = f"manual_audiovis_{time.strftime('%Y%m%d%H%M%S')}"
    job_output_dir =  os.path.join(config.output_dir, job_name)
    hybrid_frame_path = os.path.join(job_output_dir, "inputframes")
    os.makedirs(hybrid_frame_path, exist_ok=True)   

    # Start projectM and monitor it on a background thread.
    projectm_process = run_projectm(
        input_audio = audio_file_path,
        host_output_path = hybrid_frame_path,
        preset = MILKDROP_PRESET,
        fps = FPS
    )
    if projectm_process is None:
        logger.error("ProjectM process failed to start. Exiting.")
        exit(1)
    thread = threading.Thread(target=monitor_projectm, args=(projectm_process,))
    thread.start()

    # Run Deforum pipeline on main thread while projectM runs in the background
    pipeline = DeforumAnimationPipeline.from_civitai("125703")

    args = load_settings(BASE_DEFORUM_PRESET)
    args["outdir"] = job_output_dir
    args["batch_name"] = job_name    
    args["max_frames"] = expected_frame_count
    args["width"] = WIDTH
    args["height"] = HEIGHT
    args["fps"] = FPS
    args["add_soundtrack"] = "File"
    args["soundtrack_path"] = audio_file_path
    args["seed"] = 10
    args["sampler"] ="DPM++ SDE Karras"
    args["prompts"] = {"0": "A solo delorean speeding on an ethereal highway through time jumps, like in the iconic movie back to the future."}
    args["hybrid_generate_inputframes"] = False
    args["hybrid_composite"] = "Normal"
    args["hybrid_comp_alpha_schedule"] = "0:(0.2)"
    args["hybrid_motion"] = "Optical Flow" 
    args["hybrid_flow_factor_schedule"] = "0:(1)"
    args["hybrid_motion_use_prev_img"] = True
    args["hybrid_use_first_frame_as_init_image"] = False
    args["hybrid_flow_method"] = "Farneback"
    args["diffusion_cadence"] = 1

    gen = pipeline(**args)

    logger.info(f"Output video: {gen.video_path}")
