import math, time, os
import numpy as np
from scipy.signal import savgol_filter
from typing import Callable
from PIL import Image, ImageDraw
import numpy as np
import cv2

from modules.ui import plaintext_to_html
import modules.shared as shared
from modules.paths_internal import script_path

from .helpers import (
    fix_env_Path_ffprobe,
    closest_upper_divisible_by_eight,
    load_model_from_setting,
    do_upscaleImg,
)
from .sd_helpers import renderImg2Img, renderTxt2Img
from .image import shrink_and_paste_on_blank
from .video import ContinuousVideoWriter
from .InfZoomConfig import InfZoomConfig
 
class InfZoomer:
    def __init__(self, config: InfZoomConfig) -> None:
        self.C = config
        self.prompts = {}
        self.main_frames = []
        self.out_config = {}

        for x in self.C.prompts_array:
            try:
                key = int(x[0])
                value = str(x[1])
                self.prompts[key] = value
            except ValueError:
                pass

        assert len(self.C.prompts_array) > 0, "prompts is empty"

        fix_env_Path_ffprobe()
        self.out_config = self.prepare_output_path()

        self.current_seed = self.C.seed

        # knowing the mask_height and desired outputsize find a compromise due to align 8 contraint of diffuser
        self.width = closest_upper_divisible_by_eight(self.C.outputsizeW)
        self.height = closest_upper_divisible_by_eight(self.C.outputsizeH)

        if self.width > self.height:
            self.mask_width  = self.C.outpaint_amount_px 
            self.mask_height = math.trunc(self.C.outpaint_amount_px * self.height/self.width)  
        else:
            self.mask_height  = self.C.outpaint_amount_px  
            self.mask_width  = math.trunc(self.C.outpaint_amount_px * self.width/self.height)  

        # here we leave slightly the desired ratio since if size+2*mask_size % 8 != 0
        # distribute "aligning pixels" to the mask size equally. 
        # only consider mask_size since image size is alread 8-aligned
        self.mask_width -= self.mask_width % 4
        self.mask_height -= self.mask_height % 4

        assert 0 == (2*self.mask_width+self.width) % 8
        assert 0 == (2*self.mask_height+self.height) % 8

        print (f"Adapted sizes for diffusers to: {self.width}x{self.height}+mask:{self.mask_width}x{self.mask_height}. New ratio: {(self.width+self.mask_width)/(self.height+self.mask_height)} ")

        self.num_interpol_frames = round(self.C.video_frame_rate * self.C.zoom_speed) - 1 # keyframe not to be interpolated

        if (self.C.outpaintStrategy == "Corners"):
            self.fnOutpaintMainFrames = self.outpaint_steps_cornerStrategy
            self.fnInterpolateFrames = self.interpolateFramesOuterZoom
        elif (self.C.outpaintStrategy == "Center"):
           self.fnOutpaintMainFrames = self.outpaint_steps_v8hid
           self.fnInterpolateFrames = self.interpolateFramesSmallCenter
        else:
            raise ValueError("Unsupported outpaint strategy in Infinite Zoom")

        self.outerZoom = True    # scale from overscan to target viewport

    # object properties, different from user input config
    out_config = {}
    prompts = {}
    main_frames:Image = []

    outerZoom: bool
    mask_width: int
    mask_height: int
    current_seed: int
    contVW: ContinuousVideoWriter
    fnOutpaintMainFrames: Callable
    fnInterpolateFrames: Callable

    def create_zoom(self):
        for i in range(self.C.batchcount):
            print(f"Batch {i+1}/{self.C.batchcount}")
            result = self.create_zoom_single()
        return result

    def create_zoom_single(self):

        self.main_frames.append(self.prepareInitImage())

        load_model_from_setting("infzoom_inpainting_model", self.C.progress, "Loading Model for inpainting/img2img: ")

        processed = self.fnOutpaintMainFrames()

        if (self.C.upscale_do): 
            self.doUpscaling()

        if self.C.video_zoom_mode:
            self.main_frames = self.main_frames[::-1]

        if not self.outerZoom:
            self.contVW = ContinuousVideoWriter(
                self.out_config["video_filename"], 
                self.main_frames[0],
                self.C.video_frame_rate,
                int(self.C.video_start_frame_dupe_amount), 
                self.C.video_ffmpeg_opts
            )
        
        self.fnInterpolateFrames() # changes main_frame and writes to video

        print("Video saved in: " + os.path.join(script_path, self.out_config["video_filename"]))

        return (
            self.out_config["video_filename"],
            self.main_frames,
            processed.js(),
            plaintext_to_html(processed.info),
            plaintext_to_html(""),
        )

    def doUpscaling(self):
        for idx,mf in enumerate(self.main_frames):
            print (f"\033[KInfZoom: Upscaling mainframe: {idx}   \r",end="")
            self.main_frames[idx]=do_upscaleImg(mf, self.C.upscale_do, self.C.upscaler_name, self.C.upscale_by)

        self.mask_width = math.trunc(self.mask_width*self.C.upscale_by)
        self.mask_height = math.trunc(self.mask_height *self.C.upscale_by)

        if self.C.outpaintStrategy == "Corners":
            self.width  = self.main_frames[0].width-2*self.mask_width 
            self.height = self.main_frames[0].height-2*self.mask_height
        else:
            self.width  = self.main_frames[0].width
            self.height = self.main_frames[0].height

    def prepareInitImage(self) -> Image:
        if self.C.custom_init_image:
            current_image = Image.new(mode="RGBA", size=(self.width, self.height))
            current_image = current_image.convert("RGB")
            current_image = cv2_to_pil(cv2.resize(
                    pil_to_cv2(self.C.custom_init_image),
                    (self.width, self.height),
                    interpolation=cv2.INTER_AREA            
                )
            )
            self.save2Collect(current_image, f"init_custom.png")
        else:
            load_model_from_setting("infzoom_txt2img_model", self.C.progress, "Loading Model for txt2img: ")

            processed, newseed = self.renderFirstFrame()

            if len(processed.images) > 0:
                current_image = processed.images[0]
                self.save2Collect(current_image, f"init_txt2img.png")
            self.current_seed = newseed
        return current_image

    def renderFirstFrame(self):
        pr = self.getInitialPrompt()

        return renderTxt2Img(
                f"{self.C.common_prompt_pre}\n{pr}\n{self.C.common_prompt_suf}".strip(),
                self.C.negative_prompt,
                self.C.sampler,
                self.C.num_inference_steps,
                self.C.guidance_scale,
                self.current_seed,
                self.width,
                self.height
        )

    def getInitialPrompt(self):
        return self.prompts[min(k for k in self.prompts.keys() if k >= 0)]
    

    def outpaint_steps_cornerStrategy(self):
        currentImage = self.main_frames[-1]

        # just 30 radius to get inpaint connected between outer and innter motive
        masked_image = create_mask_with_circles(
            currentImage, 
            self.mask_width, self.mask_height, 
            overmask=self.C.overmask, 
            radius=min(self.mask_height,self.mask_height)*0.2
        )

        new_width= masked_image.width
        new_height=masked_image.height

        outpaint_steps=self.C.num_outpainting_steps
        for i in range(outpaint_steps):
            print (f"Outpaint step: {str(i + 1)}/{str(outpaint_steps)} Seed: {str(self.current_seed)}")
            currentImage = self.main_frames[-1]

            if self.C.custom_exit_image and ((i + 1) == outpaint_steps):
                currentImage = cv2_to_pil(cv2.resize(
                    pil_to_cv2(self.C.custom_exit_image),
                    (self.C.width, self.C.height), 
                    interpolation=cv2.INTER_AREA
                    )
                )
                
                if 0 == self.outerZoom:
                    self.main_frames.append(currentImage.convert("RGB"))

                self.save2Collect(currentImage, self.out_config, f"exit_img.png")
            else:
                expanded_image = cv2_to_pil(
                    cv2.resize(pil_to_cv2(currentImage),
                             (new_width,new_height),
                             interpolation=cv2.INTER_AREA
                    )
                )

                #expanded_image = Image.new("RGB",(new_width,new_height),"black")
                expanded_image.paste(currentImage, (self.mask_width,self.mask_height))
                pr = self.prompts[max(k for k in self.prompts.keys() if k <= i)]
                
                processed, newseed = renderImg2Img(
                    f"{self.C.common_prompt_pre}\n{pr}\n{self.C.common_prompt_suf}".strip(),
                    self.C.negative_prompt,
                    self.C.sampler,
                    self.C.num_inference_steps,
                    self.C.guidance_scale,
                    -1, # try to avoid massive repeatings: self.current_seed,
                    new_width,  #outpaintsizeW
                    new_height,  #outpaintsizeH
                    expanded_image,
                    masked_image,
                    self.C.inpainting_denoising_strength,
                    self.C.inpainting_mask_blur,
                    self.C.inpainting_fill_mode,
                    False, # self.C.inpainting_full_res,
                    0 #self.C.inpainting_padding,
                )
                #
                
                if len(processed.images) > 0:
                    expanded_image = processed.images[0]
                    zoomed_img = cv2_to_pil(cv2.resize(
                        pil_to_cv2(expanded_image),
                        (self.width,self.height), 
                        interpolation=cv2.INTER_AREA
                        )
                    )
                        
                    if self.outerZoom:
                        self.main_frames[-1] = expanded_image # replace small image
                        self.save2Collect(processed.images[0], f"outpaint_step_{i}.png")
                        
                        if (i < outpaint_steps-1):
                            self.main_frames.append(zoomed_img)   # prepare next frame with former content

                    else:
                        zoomed_img = cv2_to_pil(cv2.resize(
                                expanded_image,
                                (self.width,self.height),
                                interpolation=cv2.INTER_AREA
                            )
                        )
                        self.main_frames.append(zoomed_img)
                        processed.images[0]=self.main_frames[-1]
                        self.save2Collect(processed.images[0], f"outpaint_step_{i}.png")

        return processed
    

    def outpaint_steps_v8hid(self):

        for i in range(self.C.num_outpainting_steps):
            print (f"Outpaint step: {str(i + 1)} / {str(self.C.num_outpainting_steps)} Seed: {str(self.current_seed)}")
        
            current_image = self.main_frames[-1]
            current_image = shrink_and_paste_on_blank(
                current_image, self.mask_width, self.mask_height
            )

            mask_image = np.array(current_image)[:, :, 3]
            mask_image = Image.fromarray(255 - mask_image).convert("RGB")

            if self.C.custom_exit_image and ((i + 1) == self.C.num_outpainting_steps):
                current_image = cv2_to_pil(
                    cv2.resize( pil_to_cv2(
                        self.C.custom_exit_image),
                        (self.width, self.height), 
                        interpolation=cv2.INTER_AREA)
                )
                
                self.main_frames.append(current_image.convert("RGB"))
                # print("using Custom Exit Image")
                self.save2Collect(current_image, f"exit_img.png")
            else:
                pr = self.prompts[max(k for k in self.prompts.keys() if k <= i)]
                processed, newseed = renderImg2Img(
                    f"{self.C.common_prompt_pre}\n{pr}\n{self.C.common_prompt_suf}".strip(),
                    self.C.negative_prompt,
                    self.C.sampler,
                    self.C.num_inference_steps,
                    self.C.guidance_scale,
                    self.current_seed,
                    self.width,
                    self.height,
                    current_image,
                    mask_image,
                    self.C.inpainting_denoising_strength,
                    self.C.inpainting_mask_blur,
                    self.C.inpainting_fill_mode,
                    self.C.inpainting_full_res,
                    self.C.inpainting_padding,
                )

                if len(processed.images) > 0:
                    self.main_frames.append(processed.images[0].convert("RGB"))
                    self.save2Collect(processed.images[0], f"outpain_step_{i}.png")
                seed = newseed
                # TODO: seed behavior

        return processed

    def calculate_interpolation_steps_linear(self, original_size, target_size, steps):
        width, height = original_size
        target_width, target_height = target_size

        if width <= 0 or height <= 0 or target_width <= 0 or target_height <= 0 or steps <= 0:
            return []

        width_step = (width - target_width) / (steps+1)     #+1 enforce steps BETWEEN keyframe, dont reach the target size. interval  like []
        height_step = (height - target_height) / (steps+1)

        scaling_steps = [(round(width - i * width_step), round(height - i * height_step)) for i in range(1,steps+1)]
        #scaling_steps.insert(0,original_size) # initial size is in the list
        return scaling_steps

   
    def interpolateFramesOuterZoom(self):

        if 0 == self.C.video_zoom_mode:
            current_image = self.main_frames[0]
        elif 1 == self.C.video_zoom_mode:
            current_image = self.main_frames[-1]
        else:
            raise ValueError("unsupported Zoom mode in INfZoom")

        outzoomSize = (self.width+self.mask_width*2, self.height+self.mask_height*2)
        target_size = (self.width, self.height) # mask border, hide blipping

        scaling_steps = self.calculate_interpolation_steps_linear(outzoomSize, target_size, self.num_interpol_frames)
        print(f"Before: {scaling_steps}, length: {len(scaling_steps)}")

        # all sizes EVEN
        for i,s in enumerate(scaling_steps):
            scaling_steps[i] = (s[0]+s[0]%2, s[1]+s[1]%2)
            # ODD steps producing jumps. even steps on even resolution is what we need.
            #scaling_steps[i] = (s[0]+1, s[1]+1)

        print(f"After EVEN: {scaling_steps}, length: {len(scaling_steps)}")


        def calculate_differences(lst):
            # Es wird eine leere Liste initialisiert, in der die Differenzen gespeichert werden
            diff_lst = []

            # Durchlaufen der Liste
            for i in range(1, len(lst)):
                # Differenz zwischen aufeinanderfolgenden Tupeln berechnen
                diff = (lst[i][0]-lst[i-1][0], lst[i][1]-lst[i-1][1])
                # Die Differenz zum diff_lst hinzufügen
                diff_lst.append(diff)

            return diff_lst

        # Beispielliste von Tupeln
        print(calculate_differences(scaling_steps))




        print ("Ratios:")
        for s in scaling_steps:
            print(f"{str(s[0]/s[1])}",end=";")

        self.contVW = ContinuousVideoWriter(self.out_config["video_filename"], 
                                            self.cropCenterTo(current_image,(target_size)),
                                            self.C.video_frame_rate,int(self.C.video_start_frame_dupe_amount-1),
                                            self.C.video_ffmpeg_opts)

        for i in range(len(self.main_frames)):
            if 0 == self.C.video_zoom_mode:
                current_image = self.main_frames[0+i]
            else:
                current_image = self.main_frames[-1-i]

            lastFrame = self.cropCenterTo(current_image,target_size)

            self.contVW.append([lastFrame])

            cv2_image = pil_to_cv2(current_image)

            # Resize and crop using OpenCV2
            for j in range(self.num_interpol_frames):
                print(f"\033[KInfZoom: Interpolate frame(CV2): main/inter: {i}/{j}   \r", end="")
                resized_image = cv2.resize(
                    cv2_image,
                    (scaling_steps[j][0], scaling_steps[j][1]),
                    interpolation=cv2.INTER_AREA
                )
                cropped_image_cv2 = cv2_crop_center(resized_image, target_size)
                cropped_image_pil = cv2_to_pil(cropped_image_cv2)
                
                self.contVW.append([cropped_image_pil])
                lastFrame = cropped_image_pil
            
        self.contVW.finish(lastFrame, int(self.C.video_last_frame_dupe_amount))

        """ USING PIL:
        for i in range(len(self.main_frames)):
            if 0 == self.C.video_zoom_mode:
                current_image = self.main_frames[0+i]
            else:
                current_image = self.main_frames[-1-i]

            self.contVW.append([
                self.cropCenterTo(current_image,(self.width, self.height))
            ])

            # interpolation steps between 2 inpainted images (=sequential zoom and crop)
            for j in range(self.num_interpol_frames - 1):
                print (f"\033[KInfZoom: Interpolate frame: main/inter: {i}/{j}   \r",end="")
                #todo: howto zoomIn when writing each frame; self.main_frames are inverted, howto interpolate?
                scaled_image = current_image.resize(scaling_steps[j], Image.LANCZOS)                    
                cropped_image = self.cropCenterTo(scaled_image,(self.width, self.height))

                self.contVW.append([cropped_image])
        """

    def interpolateFramesSmallCenter(self):

        if self.C.video_zoom_mode:
            firstImage = self.main_frames[0]
        else:
            firstImage = self.main_frames[-1]

        self.contVW = ContinuousVideoWriter(self.out_config["video_filename"], 
                                (firstImage,(self.width,self.height)),
                                self.C.video_frame_rate,int(self.C.video_start_frame_dupe_amount),
                                self.C.video_ffmpeg_opts)

        for i in range(len(self.main_frames) - 1):
            # interpolation steps between 2 inpainted images (=sequential zoom and crop)
            for j in range(self.num_interpol_frames - 1):

                print (f"\033[KInfZoom: Interpolate frame: main/inter: {i}/{j}   \r",end="")
                #todo: howto zoomIn when writing each frame; self.main_frames are inverted, howto interpolate?
                if self.C.video_zoom_mode:
                    current_image = self.main_frames[i + 1]
                else:
                    current_image = self.main_frames[i + 1]


                interpol_image = current_image
                self.save2Collect(interpol_image, f"interpol_img_{i}_{j}].png")

                interpol_width = math.ceil(
                    ( 1 - (1 - 2 * self.mask_width / self.width) **(1 - (j + 1) / self.num_interpol_frames) ) 
                    * self.width / 2
                )

                interpol_height = math.ceil(
                    ( 1 - (1 - 2 * self.mask_height / self.height) ** (1 - (j + 1) / self.num_interpol_frames) )
                    * self.height/2
                )

                interpol_image = interpol_image.crop(
                    (
                        interpol_width,
                        interpol_height,
                        self.width - interpol_width,
                        self.height - interpol_height,
                    )
                )

                interpol_image = interpol_image.resize((self.width, self.height))
                self.save2Collect(interpol_image, f"interpol_resize_{i}_{j}.png")

                # paste the higher resolution previous image in the middle to avoid drop in quality caused by zooming
                interpol_width2 = math.ceil(
                    (1 - (self.width - 2 * self.mask_width) / (self.width - 2 * interpol_width))
                    / 2 * self.width
                )

                interpol_height2 = math.ceil(
                    (1 - (self.height - 2 * self.mask_height) / (self.height - 2 * interpol_height))
                    / 2 * self.height
                )

                prev_image_fix_crop = shrink_and_paste_on_blank(
                    self.main_frames[i], interpol_width2, interpol_height2
                )

                interpol_image.paste(prev_image_fix_crop, mask=prev_image_fix_crop)
                self.save2Collect(interpol_image, f"interpol_prevcrop_{i}_{j}.png")

                self.contVW.append([interpol_image])

            self.contVW.append([current_image])


    def prepare_output_path(self):
        isCollect = shared.opts.data.get("infzoom_collectAllResources", False)
        output_path = shared.opts.data.get("infzoom_outpath", "outputs")

        save_path = os.path.join(
            output_path, shared.opts.data.get("infzoom_outSUBpath", "infinite-zooms")
        )

        if isCollect:
            save_path = os.path.join(save_path, "iz_collect" + str(int(time.time())))

        if not os.path.exists(save_path):
            os.makedirs(save_path)

        video_filename = os.path.join(
            save_path, "infinite_zoom_" + str(int(time.time())) + ".mp4"
        )

        return {
            "isCollect": isCollect,
            "save_path": save_path,
            "video_filename": video_filename,
        }


    def save2Collect(self, img, name):
        if self.out_config["isCollect"]:
            img.save(f'{self.out_config["save_path"]}/{name}.png')


    def frame2Collect(self,all_frames):
        self.save2Collect(all_frames[-1], self.out_config, f"frame_{len(all_frames)}")


    def frames2Collect(self, all_frames):
        for i, f in enumerate(all_frames):
            self.save2Collect(f, self.out_config, f"frame_{i}")


    def crop_inner_image(self, outpainted_img, width_offset, height_offset):
        width, height = outpainted_img.size

        center_x, center_y = int(width / 2), int(height / 2)

        # Crop the image to the center
        cropped_img = outpainted_img.crop(
            (
                center_x - width_offset,
                center_y - height_offset,
                center_x + width_offset,
                center_y + height_offset,
            )
        )
        prev_step_img = cropped_img.resize((width, height), resample=Image.LANCZOS)
        # resized_img = resized_img.filter(ImageFilter.SHARPEN)

        return prev_step_img

    def cropCenterTo(self, im: Image, toSize: tuple[int,int]):
        width, height = im.size
        left = (width - toSize[0])//2
        top = (height - toSize[1])//2
        right = (width + toSize[0])//2
        bottom = (height + toSize[1])//2
        return im.crop((left, top, right, bottom))

def create_mask_with_circles(original_image, border_width, border_height, overmask: int, radius=4):
    # Create a new image with border and draw a mask
    new_width = original_image.width + 2 * border_width
    new_height = original_image.height + 2 * border_height

    # Create new image, default is black
    mask = Image.new('RGB', (new_width, new_height), 'white')

    # Draw black rectangle
    draw = ImageDraw.Draw(mask)
    draw.rectangle([border_width+overmask, border_height+overmask, new_width - border_width-overmask, new_height - border_height-overmask], fill='black')

    # Coordinates for circles
    circle_coords = [
        (border_width, border_height),  # Top-left
        (new_width - border_width, border_height),  # Top-right
        (border_width, new_height - border_height),  # Bottom-left
        (new_width - border_width, new_height - border_height),  # Bottom-right
        (new_width // 2, border_height),  # Middle-top
        (new_width // 2, new_height - border_height),  # Middle-bottom
        (border_width, new_height // 2),  # Middle-left
        (new_width - border_width, new_height // 2)  # Middle-right
    ]

    # Draw circles
    for coord in circle_coords:
        draw.ellipse([coord[0] - radius, coord[1] - radius, coord[0] + radius, coord[1] + radius], fill='white')
    return mask





def pil_to_cv2(image):
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

def cv2_to_pil(image):
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

def cv2_crop_center(img, toSize: tuple[int,int]):
    y,x = img.shape[:2]
    startx = x//2-(toSize[0]//2)
    starty = y//2-(toSize[1]//2)    
    return img[starty:starty+toSize[1],startx:startx+toSize[0]]
