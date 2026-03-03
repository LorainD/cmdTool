## 预期结果
一个类似sweagent/claudecode的命令行工具

## 关键词
[1] 自进化：能够从已有知识库和实际的工作中**得到启发**，生成凝练的可复用经验
	得到启发：总结提炼和发展（IR与模式库）
	keys：（1）从轨迹和结果中总结复用模式 （2）用什么形势来高效组织复用模式 （3）如何实现自进化？
[2] 适用于软硬结合的底层汇编代码生成任务
	这意味着这个模型需要有什么特别的能力：需要考虑硬件特性（尤其是寄存器空间）……
[3] 自动化：计划、搜索、生成、测试、debug

## 这个框架应该具备的功能和流程以及权限

1. 类似openhands，定义一个工作区（workspace）需要进行修改等的源库文件，例如ffmpeg的整个项目文件夹应该放在其中）
2. 阅读使用者的需求，进行需求拆解
3. 根据需求进行plan环节，必要时阅读工作区中的文件--*针对代码迁移工作，plan环节的自由度或许可以不用太高，特别是针对ffmpeg的迁移，不考虑泛用性的情况下，迁移的plan就应该是【找到需要迁移的模块的源文件（c）和x86/arm参考---实现rvv汇编和init接口函数---在源文件中添加rvv入口，在makefile中添加新的rvv实现和接口声明文件链接---交叉编译，链接，运行checkasm测试---运行fate测试（尤为注意的是，checkasm只能表明对或是错。此时可以通过修改checkasm添加输出来进一步明确错误；同时，这个流程中没有自进化的内容，应该在实现rvv的时候添加对模式库的检索等自进化相关的流程】*
4. 根据plan执行，能够具备
	1. **检索**到需要的文件--**解读**文件，对源文件的任务进行模板化**总结**，对源文件（x86/arm)采取的模式进行总结和判断是否适用于rvv，并在过往模式库中寻找经验，如果没有找到类似的，就在模板库中**新建**，将本次实验作为经验保存下来
	2. 能够**生成**rvv文件
	3. 能够将生成文件放到相应的位置，并且对相关文件（makefile、.h)进行修改
	4. 能够自主交叉**编译**【首先攻克linux平台+qemu仿真/或者用4090本地化链接测试板？】
	5. 自主**运行**checkasm
	6. 如果通过，能够从生成文件中**提取**模式放入模式库，或是对已有的模式**添加权重/去重**（在运行时就整理好模式库，此处可以添加人工辅助：决定是否要放入）
	7. 如果不通过，先**自我迭代**--进一步**修改checkasm增加输出**，同时检索---如果还是不行？**报备**人类专家解决？同时也把这个问题的模式放入模式库中“模型可能无法解决”，也就是知识边界中的“超过模型边界”但是在人类知识内的部分
	8. 生成文档表明全过程的意图

### 核心模块

1️⃣ Planning Agent
输入：
- 用户指令：迁移 ff_vp8_idct16_add
- workspace：存放ffmpeg库

输出：
- 迁移步骤plan
固定plan模板即可（不必过度自由）


2️⃣ Analysis Agent
负责：
- 找到 C 实现
- 找到 x86 / ARM 实现
- 提取：
    - 数据宽度
    - stride模式
    - 是否存在overlap
    - vector pattern
    - reduction pattern
    - 实际功能实现（自然语言+数学语言，精准简练描述）

输出：
结构化任务描述（你的“功能抽象层”）

3️⃣ Pattern Retrieval Agent（关键）

- 在模式库中检索：
    - 横向累加模式
    - butterfly模式
    - stride load模式
    - saturate模式
    - 尾处理模式
    - 根据实际功能实现寻找类似示例

如果匹配：
→ 提供生成模板
如果不匹配：
→ 新建pattern

这才是“自进化”的真正核心。

 4️⃣ Code Generation Agent
基于：
- 抽象描述
- 匹配到的pattern

生成：
- RVV asm
- init函数
- header
- Makefile patch

5️⃣ Execution Agent

自动：
- 交叉编译
- qemu运行/命令行链接测试板运行（待定）
- checkasm
- fate

6️⃣ Debug Agent
如果失败：
- 修改checkasm输出
- 对比差值
- 重新生成
- 若3轮失败 → 上报人类

 7️⃣ Pattern Update Agent

成功：
- 抽取生成模式
- 写入模式库
- 更新权重

失败：
- 记录为“超出能力边界模式”

---

#### 模块细节补充

<mark style="background: #ADCCFFA6;">1️⃣固定plan模板：</mark>
1. 定位C实现
2. 定位x86/ARM参考实现
3. 进行语义抽象
4. 检索模式库
5. 生成RVV实现
6. 修改init接口
7. 修改Makefile
8. 编译
9. checkasm
10. fate
11. 更新模式库


<mark style="background: #ADCCFFA6;">2️⃣ 分析：结构化语义描述</mark>
```
{
  "datatype": "int16",
  "vectorizable": true,
  "pattern": ["butterfly", "horizontal_add"],
  "has_stride": true,
  "has_saturation": true,
  "reduction": false,
  "tail_required": true,
  "math_expression": "y[i] = clip((a[i] + b[i]) >> 3)"
}

```


<mark style="background: #ADCCFFA6;">2️⃣ skill的加入：验证和增强结构化信息</mark>

analysis_skills/
 ├── detect_stride.py
 ├── detect_reduction.py
 ├── detect_butterfly.py
 ├── detect_overlap.py
 ├── detect_saturate.py


**<mark style="background: #ADCCFFA6;">3️⃣Pattern lib：自进化核心，轻量的记忆管理与动态上下文构建</mark>**

定义pattern
```
pattern_db/
 ├── horizontal_add.json
 ├── butterfly.json
 ├── stride_load.json
 ├── saturate.json
 ├── tail_processing.json
```

```
{
  "description": "...",
  "rvv_template": "...",
  "constraints": "...",
  "usage_count": 3,
  "success_rate": 0.8
}
```


4️⃣生成之前检测top2 lib，使用模板 or llm 自行发挥
5️⃣纯工程执行agent
6️⃣debug agent：修改和伪差分测试


if checkasm fail:
    修改checkasm打印差值
    提供错误输出给LLM
    重新生成
最多3轮

```
也可以加入skill--bug模式库（预后的错误处理，相比于pattern lib更像pre ）

#### 权限设计

读写workspace、修改源码、修改makefile、shell执行、编译、运行二进制


## 实现计划：
#### （1）实现自动化流程
1. 调用llm进行意图解析：识别到是要迁移哪个算子。并且检索到应该参考和相关的文件（c源文件以及头文件、x86参考文件、arm参考文件，以及makefile、checkasm文件）
2. 调用llm进行rvv生成，生成rvv文件和头文件。或是在已有的头文件中生成新的函数声明
3. 执行交叉编译和测试
在/workplace/FFmpeg下创建build文件夹，在该文件夹下进行build操作
交叉编译命令应该为
```
/home/yuhe/project/original/FFmpeg/configure   --cross-prefix=riscv64-unknown-linux-gnu-   --arch=riscv64   --target-os=linux   --enable-cross-compile   --cpu=rv64gcv   --extra-cflags="-march=rv64gcv -mabi=lp64d -O3"   --extra-ldflags="-static"   --disable-shared   --enable-static

```
```
make -j$(nproc) tests/checkasm/checkasm

```

4. 测试checkasm：
（1）方式一
连接rvv测试版：
```
ssh musepi@192.168.31.124

```

cd workplace/

scp -rC yuhe@10.30.20.14:/home/yuhe/project/original/FFmpeg/build/tests/checkasm/checkasm .
在测试板上运行 ./checkasm

（2）下载qemu仿真

