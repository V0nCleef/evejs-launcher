"""Generate hero banner assets for EveJS Launcher V2 via ComfyUI API."""
import json
import urllib.request
import urllib.parse
import time
import os
import sys
from pathlib import Path

COMFY_HOST = "http://127.0.0.1:8188"
OUTPUT_DIR = Path(__file__).parent.parent / "assets" / "hero"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# SD 1.5 txt2img workflow template
WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 25,
            "cfg": 7.5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "dreamshaper_8.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 256, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["4", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "blurry, low quality, text, watermark, signature, ugly, deformed, bright, white background, cartoon, anime, 3d render, people, faces",
            "clip": ["4", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "evejs_hero", "images": ["8", 0]},
    },
}

NEGATIVE = "blurry, low quality, text, watermark, signature, ugly, deformed, bright, white background, cartoon, anime, 3d render, people, faces, oversaturated, neon pink"

BANNERS = [
    {
        "name": "hero_fleet",
        "prompt": "cinematic wide shot of a massive spaceship fleet in deep space, dark void, teal engine glow, gold accent lights, EVE Online style, photorealistic, dramatic lighting, dark color palette with deep blacks, sci-fi concept art, 8k, highly detailed",
        "seed": 42001,
    },
    {
        "name": "hero_station",
        "prompt": "cinematic shot of a massive space station orbiting a dark planet, teal holographic displays, gold window lights, EVE Online style, photorealistic, moody atmosphere, deep space background, dark color palette, sci-fi concept art, 8k, highly detailed",
        "seed": 42002,
    },
    {
        "name": "hero_nebula",
        "prompt": "cinematic view of a dark nebula in deep space, teal and gold gas clouds, distant stars, EVE Online style, photorealistic, mysterious atmosphere, dark color palette with deep blacks, sci-fi concept art, 8k, highly detailed",
        "seed": 42003,
    },
    {
        "name": "hero_undock",
        "prompt": "cinematic shot of a spaceship undocking from a massive station, teal thruster glow, dark space background, gold station lights, EVE Online style, photorealistic, dramatic perspective, dark color palette, sci-fi concept art, 8k, highly detailed",
        "seed": 42004,
    },
    {
        "name": "hero_mining",
        "prompt": "cinematic shot of mining barges in an asteroid belt, teal laser beams, dark rocks, distant sun, EVE Online style, photorealistic, industrial sci-fi, dark color palette, sci-fi concept art, 8k, highly detailed",
        "seed": 42005,
    },
]


def queue_prompt(workflow: dict) -> str:
    """Submit workflow to ComfyUI and return prompt_id."""
    payload = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY_HOST}/api/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["prompt_id"]


def wait_for_completion(prompt_id: str, timeout: int = 300) -> dict:
    """Poll history until prompt completes."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{COMFY_HOST}/history/{prompt_id}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                history = json.loads(resp.read().decode("utf-8"))
            if prompt_id in history:
                return history[prompt_id]
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Prompt {prompt_id} did not complete in {timeout}s")


def download_image(filename: str, subfolder: str, output_path: Path) -> None:
    """Download generated image from ComfyUI."""
    params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
    url = f"{COMFY_HOST}/api/view?{params}"
    urllib.request.urlretrieve(url, output_path)


def generate_banner(banner: dict) -> Path | None:
    """Generate a single hero banner."""
    print(f"  Generating {banner['name']}...")

    # Clone workflow and inject parameters
    wf = json.loads(json.dumps(WORKFLOW))
    wf["3"]["inputs"]["seed"] = banner["seed"]
    wf["6"]["inputs"]["text"] = banner["prompt"]
    wf["7"]["inputs"]["text"] = NEGATIVE
    wf["9"]["inputs"]["filename_prefix"] = f"evejs_{banner['name']}"

    try:
        prompt_id = queue_prompt(wf)
        print(f"    Queued: {prompt_id}")

        result = wait_for_completion(prompt_id, timeout=300)

        # Find output image
        outputs = result.get("outputs", {})
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img in node_output["images"]:
                    filename = img["filename"]
                    subfolder = img.get("subfolder", "")
                    output_path = OUTPUT_DIR / f"{banner['name']}.png"
                    download_image(filename, subfolder, output_path)
                    print(f"    Saved: {output_path}")
                    return output_path

        print(f"    ERROR: No images in output for {banner['name']}")
        return None

    except Exception as e:
        print(f"    ERROR: {e}")
        return None


def main():
    print("EveJS Launcher V2 — Hero Asset Generation")
    print(f"ComfyUI: {COMFY_HOST}")
    print(f"Output: {OUTPUT_DIR}")
    print()

    # Check ComfyUI is reachable
    try:
        req = urllib.request.Request(f"{COMFY_HOST}/system_stats")
        with urllib.request.urlopen(req, timeout=5) as resp:
            stats = json.loads(resp.read().decode("utf-8"))
        print(f"Connected. GPU: {stats['devices'][0]['name']}")
    except Exception as e:
        print(f"ERROR: Cannot reach ComfyUI: {e}")
        sys.exit(1)

    # Check model exists
    try:
        req = urllib.request.Request(f"{COMFY_HOST}/models/checkpoints")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = json.loads(resp.read().decode("utf-8"))
        if "dreamshaper_8.safetensors" not in models:
            print(f"ERROR: dreamshaper_8.safetensors not found. Available: {models}")
            sys.exit(1)
        print("Model: dreamshaper_8.safetensors ✓")
    except Exception as e:
        print(f"ERROR: Cannot list models: {e}")
        sys.exit(1)

    print()
    generated = []
    for banner in BANNERS:
        path = generate_banner(banner)
        if path:
            generated.append(path)
        print()

    print(f"Generated {len(generated)}/{len(BANNERS)} banners:")
    for p in generated:
        size_kb = p.stat().st_size / 1024
        print(f"  {p.name}: {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
