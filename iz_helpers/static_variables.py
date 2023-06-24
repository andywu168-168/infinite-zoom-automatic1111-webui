import os
from modules import scripts
import modules.sd_samplers

default_sampling_steps = 35
default_sampler = "DDIM"
default_cfg_scale = 8
default_mask_blur = 48
default_gradient_size = 61
default_overmask = 8
default_total_outpaints = 3
default_outpaint_amount = 128
promptTableHeaders = ["Outpaint Steps", "Prompt", "image location", "blend mask", "is keyframe"], ["number", "str", "str", "str", "bool"]
default_lut_example_img = "extensions\\infinite-zoom-automatic1111-webui\\LUT\\daisy.jpg"
default_Luma_wipe_img = "extensions\\infinite-zoom-automatic1111-webui\\Wipes\\clock.png"

default_prompt = """
{
    "prePrompt":"(((Best quality))), ((masterpiece)), ",
    "prompts":{
        "headers":["Start at second [0,1,...]","prompt","image location","blend mask location", "is keyframe"],
        "data":[
            [0, "Huge spectacular Waterfall in a dense tropical forest,epic perspective,(vegetation overgrowth:1.3)(intricate, ornamentation:1.1),(baroque:1.1), fantasy, (realistic:1) digital painting , (magical,mystical:1.2) , (wide angle shot:1.4), (landscape composed:1.2)(medieval:1.1), divine,cinematic,(tropical forest:1.4),(river:1.3)mythology,india, volumetric lighting, Hindu ,epic,  Alex Horley Wenjun Lin greg rutkowski Ruan Jia (Wayne Barlowe:1.2) <lora:epiNoiseoffset_v2:0.6> ","C:\\\\path\\\\to\\\\image.png", "extensions\\\\infinite-zoom-automatic1111-webui\\\\blends\\\\sun-square.png", true],
            [1, "a Lush jungle","","",false],
            [2, "a Thick rainforest","","",false],
            [3, "a crashed UFO stuck in the ground","","",false],
            [4, "a Verdant canopy","","",false]
        ]
    },
    "postPrompt": "epic perspective,(vegetation overgrowth:1.3)(intricate, ornamentation:1.1),(baroque:1.1), fantasy, (realistic:1) digital painting , (magical,mystical:1.2) , (wide angle shot:1.4), (landscape composed:1.2)(medieval:1.1),(tropical forest:1.4),(river:1.3) volumetric lighting ,epic, style by Alex Horley Wenjun Lin greg rutkowski Ruan Jia (Wayne Barlowe:1.2)",
    "negPrompt": "frames, border, edges, borderline, text, character, duplicate, error, out of frame, watermark, low quality, ugly, deformed, blur, bad-artist",
    "audioFileName": "",
    "seed":-1,
    "width": 512,
    "height": 512,
    "sampler": "DDIM",
    "guidanceScale": 8.0,
    "steps": 35,
    "lutFileName": "",
    "outpaintAmount": 128,
    "maskBlur": 48,
    "overmask": 8.0,
    "outpaintStrategy": "Corners",
    "zoomMode": "Zoom-out",
    "fps": 30,
    "zoomSpeed": 1.0,
    "startFrames": 0,
    "lastFrames": 0,
    "blendMode": "Not Used",
    "blendColor": "#ffff00",
    "blendGradient": 61,
    "blendInvert": false
}
"""

empty_prompt = (
    '{"prompts":{"data":[0,"","","",false],"headers":["Outpaintg Steps","prompt","image location", "blend mask location", "is keyframe"]},"negPrompt":"", "prePrompt":"", "postPrompt":"", "audioFileName":None, "seed":-1, "width": 512, "height": 512, "sampler": "DDIM", "guidanceScale": 8.0, "steps": 35, "lutFileName": "", "outpaintAmount": 128, "maskBlur": 48, "overmask": 8, "outpaintStrategy": "Corners", "zoomMode": "Zoom-out", "fps": 30, "zoomSpeed": 1, "startFrames": 0, "lastFrames": 0, "blendMode": "Not Used", "blendColor": "#ffff00", "blendGradient": 61, "blendInvert": false}'
)

invalid_prompt = {
    "prompts": {
        "data": [[0, "Your prompt-json is invalid, please check Settings","", "", False]],
        "headers": ["Start at second [0,1,...]", "prompt","image location","blend mask location", "is keyframe"],
    },
    "negPrompt": "Invalid prompt-json",
    "prePrompt": "Invalid prompt",
    "postPrompt": "Invalid prompt",
    "audioFileName": "",
    "seed":-1,
    "width": 512,
    "height": 512,
    "sampler": "DDIM",
    "guidanceScale": 8.0,
    "steps": 35,
    "lutFileName": "",
    "outpaintAmount": 128,
    "maskBlur": 48,
    "overmask": 8,
    "outpaintStrategy": "Corners",
    "zoomMode": "Zoom-out",
    "fps": 30,
    "zoomSpeed": 1.0,
    "startFrames": 0,
    "lastFrames": 0,
    "blendMode": "Not Used",
    "blendColor": "#ffff00",
    "blendGradient": 61,
    "blendInvert": False
}

available_samplers = [
    s.name for s in modules.sd_samplers.samplers if "UniPc" not in s.name
]

current_script_dir = scripts.basedir().split(os.sep)[
    -2:
]  # contains install and our extension foldername
jsonprompt_schemafile = (
    current_script_dir[0]
    + "/"
    + current_script_dir[1]
    + "/iz_helpers/promptschema.json"
)
