# GLM-Edge-V-2B--Monitoring_Species
Built with GLM-Edge

<p align="center">
👋 联系作者在工作邮箱：Raymond.dengruihan@edu.yungu.org
</p>
<p align="center">
或个人邮箱：551310554@qq.com
</p>
<p align="center">
或个人电话：18368725059
</p>
<p align="center">
工作邮箱不一定联系得上，如果没有及时回复请考虑另外的联系方式
</p>

## 引言
这是一个基于由智谱AI开源的端侧多模态大模型GLM-Edge-V-2b的福寿螺卵自动统计系统
本仓库的所有者为该项目的主要开发人员
该项目由2024-2025届CTB（China Thinks Big）比赛，”影视旋风“小队的创新项目发展而来，我们小队奋斗半年，最终产出了这样一个项目，我将会把我们在比赛期间内所完成的部分保存在本仓库的”Original“文件夹，保留一份我们的青春回忆以及奋斗历史。在这里感谢与我并肩作战的老师及同学们：Jie, Felcia, Alice, Carl, Robin。

## 项目更新
- 2025年5月：上传了```app.py```和```information.py```，替换了了其中的文件路径，使用中文提示
- 2025年4月：上传```original```文件，该源文件直接复制自CTB竞赛时期的电脑文件夹

## 测试平台
|       CPU      |          显卡           |      内存     |
|:--------------:|:----------------------:|:------------:|
|    E5-2678V3   |   Nvdia 2080Ti 22G*2   | 32G 2133Mhz*8|


## Quick Start
### 硬件需求
按照测试，只运行demo需空闲内存15GB，GPU显存3GB

### 环境安装
使用 pip 安装依赖：pip install -r requirements.txt

### 从本地加载模型
原始的模型应该从[🤗 Huggingface](https://huggingface.co/THUDM/glm-edge-1.5b-chat)下载
将模型下载到本地之后，将以上代码中的```将文本替换为你的模型路径```替换为你本地的```GLM-Edge—v-2B```文件夹的路径，即可从本地加载模型。
