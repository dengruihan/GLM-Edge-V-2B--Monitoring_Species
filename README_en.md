# GLM-Edge-V-2B--Monitoring_Species
Built with GLM-Edge

<p align="center">
👋 Contact the author in the working email：Raymond.dengruihan@edu.yungu.org
</p>
<p align="center">
or personal email：551310554@qq.com
</p>
<p align="center">
or personal phone number：18368725059
</p>
<p align="center">
If author didn't reply you in working email, please consider the other ways
</p>

*用[中文](README.md)阅读此文.*

## Introduction
This is an automatic system based on multimodal large language model "GLM-Edge-V-2b" which is open-sourced for edge devices by ZhiPu AI for detecting Golden Apple Snail's egg 
The owner of this repository is the major developer of this project
This project originated from the collaborative innovation initiative of 影视旋风 team (participating in CTB 2024-2025, China Thinks Big), representing a six-month development journey that culminated in the creation of this solution. I'll upload our product which has done during the journey in the folder "Original" of this repository, to keep our youth, memory and the history. Thanks to my teacher and classmates who work with me side by side: Jie, Felcia, Alice, Carl,Robin.

## NEWS📰
(Year/month/date)
- 2025.6.19: upload English Readme
- 2025.5: upload```app.py```and```information.py```, Replace the path in these files, add Chinese annotation
- 2025.4: upload```original```folder, This original files copy directly from the computer which is working during the CTB journey.

## Test Platform
|       CPU      |          GPU           |      RAM     |      OS      |
|:--------------:|:----------------------:|:------------:|:------------:|
|    E5-2678V3   |   Nvdia 2080Ti 22G*2   | 32G 2133Mhz*8|   Windows 10 |

## Quick Start
### Hardware Requirement
Running demo need RAM at least 15GB，GPU graphic memory at least 3GB

### Dependencies Prepare
Use pip install requirements：
```shell
pip install -r requirements.txt
```

### Loading Local Model
Original model should download from[🤗 Huggingface](https://huggingface.co/THUDM/glm-edge-1.5b-chat)
After downloading the model to the local environment, replace the ```将文本替换为你的模型路径```with your local folder path of```GLM-Edge—v-2B```, This allows you to Load it locally。
