# Paper Radar Digest

## 1. Curriculum is more influential than haptic feedback when learning object manipulation
- Venue: Science Advances
- Published: 2025-04-04
- Type: direct
- Tags: manipulation, mobile_robot
- Score: 0.7518
- Core insight: 在三指手无视觉仿真中，提升和旋转奖励的课程顺序比是否提供指尖三维力触觉更强地决定动态在手操作的学习路径和最终能力。
- Problem frame: 掌内动态操作需在间歇接触下抗重力，常被认为必须依赖触觉；多目标强化学习又缺少对课程安排和学习率如何共同塑造能力的直接比较。
- First principles: PPO 优化的是奖励定义的策略分布，关节角、角速度和掌部状态可通过动力学间接塑造物体运动；奖励阶段切换时学习率也应重新适配。
- Mechanism: 作者比较五种提升与旋转两阶段课程、无触觉与每指三维力两种条件；每阶段 1000 回合，并在奖励切换时采用分段线性学习率调度。
- Boundary advanced: 结论是此特定仿真任务中触觉并非充分必要条件，而非触觉普遍无用；系统仍使用物体高度与朝向，只验证三指 MuJoCo 手、固定课程和有限物体。
- Old problem: 常规直觉把课程设为从单目标到多目标，也把触觉视为动态操作必要输入；固定或单调衰减学习率在奖励切换后的再收敛较弱。
- Why it works: 先学习联合提升与旋转可形成可迁移动作基础，再聚焦单技能时细化目标；无触觉策略仍能从自身运动状态学习改变球动力学的手部运动，分段学习率兼顾探索和收敛。
- True novelty: 新意不只是报告无触觉也能操作，而是以受控的课程乘感知条件比较显示课程塑造能力轨迹，并将学习率调度与奖励阶段切换耦合。
- Evidence: 每个课程与触觉条件进行 60 个独立 PPO 试验，单试验为 2000 个 10 s 回合；球体任务中无触觉可达与三维力触觉相近表现。C5 奖励切换后的成功试验中，分段学习率平均 250 回合收敛，线性和常数学习率分别为 450 和 1000 回合。

## 2. Leaping out of the water: Aerial-aquatic locomotion with flapping wings
- Venue: Science
- Published: 2026-07-09
- Type: direct
- Tags: flapping_wing
- Score: 0.6675
- Core insight: 鸟尺度扑翼机器人表明，跨越空气与水并不必依赖折翼、足部或附加推进；翼面积、被动柔顺性、拍频、短尾和出水姿态协同即可让同一翼完成飞行、游泳与离水。
- Problem frame: 同一翼必须在空气中提供升力、在水中承受高流体载荷，并在自由液面瞬态保持姿态稳定；动物原位数据和纯仿真都难以拆开这些耦合权衡。
- First principles: 水中载荷诱发翼的被动弯曲，改变有效翼幅和翼尖速度；小翼利于水下速度却削弱起飞推力，长尾在出水时会形成俯冲力矩。
- Mechanism: 250 g 无系留平台逐一改变翼尺寸、刚度和拍频，并用尾翼调俯仰。中等翼面积与中等刚度提供跨介质折中，短尾加约 70° 出水轨迹抑制离水阶段的不稳定力矩。
- Boundary advanced: 工作给出翼尺寸、刚度、拍频、尾长和出水角的可测参数地图，并在鸟尺度完成无足、无折翼的翼驱离水，而非只展示单一介质运动。
- Old problem: 此前常把折翼或腿部推进视为跨介质的必要补偿，设计者缺少能直接比较水下效率、空中推力和过渡稳定性的共同实验基准。
- Why it works: 柔顺性把主动形态调节转成随水载自动发生的被动变形；拍频调节维持推进，中等翼避开水阻与起飞推力的两个极端，短尾和出水角稳定界面过渡。
- True novelty: 新意在于把翼柔顺、尺度、频率、尾部和轨迹放进同一无系留物理模型中，直接验证通常由额外机构补偿的翼驱离水阶段。
- Evidence: 柔性翼水中可工作至 6 Hz，刚性翼上限为 0.85 Hz；5 Hz 时小、中、大翼水下速度为 0.95、0.79、0.64 m/s。中等配置室内飞行均速 6.3 m/s，约 70° 出水最可靠且少于 1 s 离水；作者同时报告出水能量收益与电能约 1:40。

## 3. A growing soft robot with climbing plant–inspired adaptive behaviors for navigation in unstructured environments
- Venue: Science Robotics
- Published: 2024-01-17
- Type: direct
- Tags: soft_robot
- Score: 0.5887
- Core insight: FiloBot 将感知、决策和材料沉积共置于生长尖端：重力、蓝光和红远红光线索先形成向量场，再以差异化 FDM 同时决定身体转向和力学性质。
- Problem frame: 非结构化三维导航既需找到方向，也需在跨隙、悬空和支撑物之间构造足以承载自重的身体；仅靠预设形态或全局路径规划难适应现场条件。
- First principles: 尖端增材制造既延长身体，也能通过周向层高差产生曲率；打印参数决定刚度和增长速度，支撑物一旦被缠绕便能分担自重与剪切载荷。
- Mechanism: 尖端加速度计和颜色传感器读取环境梯度，控制器将其加权为趋重、向光或趋阴方向；供丝、旋转速度和温度制造差异沉积以转向，并在跨隙时提高结构强度、在依附后降低材料开销。
- Boundary advanced: 它把生长机器人从模仿单一藤蔓特征推进到感知、行为规则与在线建体一体化，身体形状在部署过程中与环境共同生成。
- Old problem: 细长生长机器人悬空时容易塌陷，面对空隙、支撑物和扰动时又缺少把局部线索直接变成结构性应对的机制。
- Why it works: 局部传感和沉积执行都在尖端，环境梯度直接转为下一段曲率，避免先重建全局地图；差异沉积把控制命令固化为承载结构，缠绕则把负载转移给环境。
- True novelty: 关键新意是把 FDM 从制造步骤变成运动、转向和力学重构的执行器，并以植物向性规则完成低计算量闭环，而非只复刻藤蔓外观。
- Evidence: FiloBot 直径 40 mm、质量 82.5 g，增长约 2–7 mm/min，最小转弯半径约 80 mm。高强度参数下最大弯矩为 57±6.51 N·m、杨氏模量变化约 9 倍；展示了 50 cm 跨隙、绕 7 cm 支撑缠绕、受扰恢复及按 FR:R 线索改向。

## 4. Physically intelligent autonomous soft robotic maze escaper
- Venue: Science Advances
- Published: 2023-09-08
- Type: direct
- Tags: soft_robot
- Score: 0.4916
- Core insight: 液晶弹性体滚动带的一端螺旋、另一端扭转，几何不对称把热驱动滚动变为持续自转向；结合撞墙扣弹反射，机器人无需传感或控制器即可试错逃出迷宫。
- Problem frame: 未知多通道迷宫通常需要感知与规划；已有扭转 LCE 带的转向在热平衡后衰减，只靠扣弹会在平行壁之间反复反射而被困。
- First principles: LCE 解扭提供的是暂态力矩，而两端直径失配近似锥体，会在滚动中持续朝较小直径端偏转；扣弹改变方向，二者叠加扩大横向搜索。
- Mechanism: 半螺旋半扭转带由 J 形前体经拉伸、扭转和二次固化制备。热台驱动滚动，几何不对称持续给出曲率，遇墙时自扣弹反转并形成弯曲锯齿轨迹。
- Boundary advanced: 它把被动滚动和单次避障推进到多通道、颗粒基底、窄缝和周期变布局中的无电子自主逃逸，并区分了稳态几何转向与暂态材料转向。
- Old problem: 纯扭转或纯螺旋带在平行通道中会重复原路径，传统刚体迷宫机器人则需显式传感、状态估计和控制。
- Why it works: 直径失配消除了热平衡后直行的系统性问题；扣弹提供离散方向翻转，因而每次碰壁后的轨迹不再重合，软体弯曲还允许短暂压缩通过窄缝。
- True novelty: 创新在于将持续主动转向、被动绕障和接触反射编码进同一软体本体，并用实验、有限元和解析模型解释为何可从被困转为搜索。
- Evidence: 在 20 cm 长、10 cm 间距的平行壁中，混合带 12 次扣弹、1000 s 内逃出，而两类对照被困；最小转弯半径约 19 cm，扫过面积为对照的 1.67 倍和 2 倍。它还能通过 7 cm 缝隙并在砂地和动态迷宫中逃逸。

## 5. Mechanical multiaxis force sensor for directly bridging sensing and fluidic actuation
- Venue: Science Advances
- Published: 2026-07-10
- Type: direct
- Tags: soft_robot, manipulation
- Score: 0.5425
- Core insight: ME-SOFS 将多轴接触力同时当作信息与驱动力：外力分解为多路液体位移并直接驱动流体执行器，反应回路无需外部计算。
- Problem frame: 软体机器人通常把接触力转成电信号，再经电路、控制器和泵阀转回气液驱动；跨物理域的链路增加了布线、供能和计算负担。
- First principles: 中央软柱受力后倾斜或下压，挤压对应腔室并排出液体；同一流体同时携带方向与幅值信息，也在末端产生压力和位移。
- Mechanism: 五腔结构以四个水平腔和一个垂直腔解耦 x、y、z 载荷，经软管把水送至末端。可选磁体金属弧读出将流量转为脉冲，用于观察而非实现基本反应。
- Boundary advanced: 它推进的不是单一柔性力传感，而是多轴力到分离流体动作的直接闭环，已用于液滴操控、纤毛阵列和触觉教学；带宽仍低于 2.5 Hz。
- Old problem: 既有压阻、磁、电容或摩擦电多轴软传感器虽能测力，但与气液执行器连接仍依赖外部信号转换与控制链路。
- Why it works: 受力本身提供流体位移能量，腔室和软管在同一物理域内传递它；各向腔室与多孔支撑减少串扰，使末端随力矢量作出相配动作。
- True novelty: 新意是将可参数化的机械传感、流体传输和执行器串成一个多轴反应回路，电子读出保留可观测性而不承担控制必需功能。
- Evidence: 45°、17.5 N 载荷下分解出 10.7 N 水平和 13.8 N 垂直分量，对应 24 和 21 个跨弧事件；峰数与力关系 R²>0.9，灵敏度可调范围超过 92 倍。还展示 10 g LEGO 抓取、液滴输运、纤毛弯曲，并在 110 kPa 水压和 90°C 水中测试。

## 6. Attention-based map encoding for learning generalized legged locomotion
- Venue: Science Robotics
- Published: 2025-08-27
- Type: direct
- Tags: humanoid, locomotion, mobile_robot
- Score: 0.9108
- Core insight: 策略以当前本体状态查询地形图，注意力自动聚焦未来可踩区域；端到端强化学习不显式运行 MPC，也能生成精确而鲁棒的腿足动作。
- Problem frame: 稀疏踏点地形要求精确落脚、对地图与模型误差鲁棒并能跨机体泛化。模型式规划精确却脆弱，普通 MLP 强化学习鲁棒却难找到稀疏可踩点。
- First principles: 可踩性取决于当前姿态、速度、关节状态与命令，而不是地形点的孤立属性；本体感觉作 query、地形特征作 key/value 可表达这种状态相关性。
- Mechanism: 二维卷积提取机器人中心高度图的点级特征，本体状态形成 query，经 64 维多头注意力得到地图编码并输出关节动作。训练先在完美感知基础地形学习，再以噪声、漂移和扰动微调。
- Boundary advanced: 同一端到端方法用于 12 自由度 ANYmal-D 和 23 自由度 GR-1，实机完成未见稀疏地形行走；注意力图提供了比黑箱 MLP 更可检查的落脚线索。
- Old problem: 基于模型的接触规划受状态估计和动力学近似影响，已有学习策略常在稀疏地形过拟合，混合方法仍需保留昂贵规划器。
- Why it works: 第一阶段先发现基本落脚技能，第二阶段扩展不确定性覆盖；注意力把高维地图压缩为随身体状态变化的关键区域，兼顾位置精度与扰动反应。
- True novelty: 关键不是简单加 Transformer，而是将点级、由本体感觉条件化的地图注意力嵌入端到端控制，并显示无监督地涌现类似未来落脚点的关注。
- Evidence: 两种机器人在未见障碍跑酷仿真中均达 100% 成功率；ANYmal-D 训练地形上成功率较 DTC 和基线 RL 分别高 26.5% 与 77.3%。实机展示跨踏石、梁、缺口和打滑恢复；局限是训练需数天、2.5D 地图不适于狭窄空间。

## 7. NavRL++: A System-Level Framework for Improving Sim-to-Real Transfer in Reinforcement Learning-Based Robot Navigation
- Venue: arXiv
- Published: 2026-05-15
- Type: direct
- Tags: locomotion, mobile_robot
- Score: 0.875
- Core insight: NavRL++ 将仿真到现实看作感知、时延和底层控制共同造成的系统级分布偏移：用扰动微调覆盖这些误差，再以短时历史 Transformer 稳定导航决策。
- Problem frame: 许多 RL 导航工作只调输入、奖励或动作形式，却未拆解真实部署中的噪声、漏检、输入动作时延和控制响应差异，导致清洁仿真策略在动态场景失效。
- First principles: 观测缺失、滞后和动力学失配使策略面对训练分布外状态；将这些扰动纳入训练并比较短时历史，才能推断运动趋势、缓冲瞬时误差和约束动作跳变。
- Mechanism: 系统以射线距离压缩静态环境，保留动态障碍和机器人状态的 2 秒历史，由 12-token Transformer 输出跨平台速度命令。微调注入传感噪声、0.3 概率漏检、输入动作时延和控制增益变化，部署可叠加 Velocity Obstacle 安全屏蔽。
- Boundary advanced: 相同训练策略部署到自制 UAV 与 Unitree 四足机，可接相机、LiDAR 或二者融合；机载端到端推理为 20 Hz、平均 4.1 ms，但底层控制、感知和可选安全屏蔽仍是成功系统的一部分。
- Old problem: 只在干净模拟器训练的单步策略易放大视觉域差异和动态目标漏检，纯优化规划器在动态场景的鲁棒性也并非天然成立。
- Why it works: 射线表示减少传感器外观差异，历史 token 可估计障碍运动和延迟状态；课程训练先获得避障，再以针对性扰动扩展状态覆盖。
- True novelty: 真正的新意是将部署误差清单、扰动感知微调、时序策略和真实感知安全链路作为一个可检验的迁移配方，而非单称 Transformer 或 RL 架构解决迁移。
- Evidence: 这是 arXiv 预印本。组合扰动的 10,000 次评估中成功率为 94.08%，对照 NavRL 为 63.05%；时序网络使控制 effort 从 0.093 降至 0.043 m/s²，扰动微调再把综合成功率从 90.54% 提至 94.08%。实机为定性展示，高密度动态和窄余量场景仍会失败。

## 8. Interacting Multiple Model Proprioceptive Odometry for Legged Robots
- Venue: arXiv
- Published: 2026-03-31
- Type: direct
- Tags: locomotion
- Score: 0.875
- Core insight: 该工作把腿足里程计的接触约束从支撑脚静止改为会滚动且可能滑移的概率模型，仅用 IMU 与关节编码器在线估计接触模式和位姿。
- Problem frame: 外感传感器退化时，本体里程计依赖支撑脚约束抑制漂移；零足速点接触假设会被有限足端、滚动和低摩擦滑移破坏。
- First principles: 球形足滚动时足端惯性系速度由角速度和半径决定，不能强行置零；滑移主要表现为足速演化不确定性增大，而非完全不同的测量结构。
- Mechanism: 误差状态滤波器纳入四足足端速度，以滚动运动学更新；并行运行正常滚动与高过程噪声滑移两模型，按创新似然更新模式概率并融合状态。
- Boundary advanced: 它提供可实时运行的纯本体估计，验证于 AlienGo 仿真和室内复杂路线；滚动模型在不平地、足部形变和表面不规则时仍只是近似。
- Old problem: 既有方法多采用静止点接触、固定阈值或单一模型；处理滚动常需额外传感器，处理滑移又容易在正常接触时过度放松约束。
- Why it works: 显式非零滚动速度消除系统性测量失配，IMM 在创新异常时再降低接触约束置信度，因而兼顾正常支撑期精度和滑移抑制。
- True novelty: 核心不只是多跑滤波器，而是将滚动足速纳入状态，并将滑移表述为足速过程噪声差异，用概率交互替代阈值切换。
- Evidence: Gazebo 平直轨迹中，滚动建模相对 IEKF 将位置 ATE/RPE 降低 43.1%/43.5%，IMM 再相对单模型滚动 ESKF 降低 17.3%/17.8%。实机复杂地形 ATE 为 0.102 m，低于 IEKF 的 0.227 m 和单模型的 0.284 m，运行 1.00 ms/步。

## 9. Swarm navigation of cyborg-insects in unknown obstructed soft terrain
- Venue: Nature Communications
- Published: 2025-01-06
- Type: direct
- Tags: swarm_robot
- Score: 0.6978
- Core insight: 工作以导游团规则控制赛博蟑螂群：局部足够拥挤时让昆虫自由运动，成员离群时才施加刺激把它拉回群体。
- Problem frame: 生物机器混合昆虫对同一刺激反应差异大，频繁刺激会习惯化；传统群体分离控制还会诱发攀爬和缠结，难在未知软地形协同。
- First principles: 局部邻居密度是群体凝聚的代理变量：密集时自然避障更安全，稀疏时才需要向领导者或群体方向纠偏维持连通。
- Mechanism: 唯一知晓目标的领导者带路；跟随者在自由运动和向群体移动规则间切换，依据目标扇区、朝向误差、速度和距离选择转向或加速刺激，电压上限 2.5 V。
- Boundary advanced: 算法理论上去中心化，实验将 20 只赛博蟑螂带过未知沙地、岩石和坡地；但位置仍由集中式 VICON 提供，机载自治定位尚未完成。
- Old problem: BOIDS 式近距离分离会对靠近个体频繁刺激，真实昆虫容易缠结；既有赛博昆虫研究也多限于单体或理想无障碍环境。
- Why it works: 自由运动把密集区域的避碰交给昆虫本能，减少缠结、耗电和习惯化；在邻居不足时再启用纠偏，避免完全放任导致解体。
- True novelty: 它针对生物执行器不确定性，把自主时间、习惯化风险、缠结安全和群体凝聚放进一套局部切换控制逻辑。
- Evidence: 20 只赛博蟑螂在 3.5×3.5 m 含岩石和坡地场地完成 10 次导航；跟随者自主度为 0.50，控制输入平均减少约 50%，相对 BOIDS 的缠结次数平均降低超过 85%。翻倒个体在邻居帮助下 4.5 s 恢复。

## 10. A non-electrical pneumatic hybrid oscillator for high-frequency multimodal robotic locomotion
- Venue: Nature Communications
- Published: 2025-02-07
- Type: direct
- Tags: none
- Score: 0.657
- Core insight: PHO 以织物气腔、旋转双稳态碳纤梁和机械切换阀，将恒定气压转成最高 51 Hz 的交替驱动与可编程节律。
- Problem frame: 无电子气动振荡器通常频率低、相位难调或串联受限；高频方案又常伴随泄气或外接执行器，限制极端环境下的多模态运动。
- First principles: 气腔充气推动主动关节，双稳态梁到临界力矩后快速屈曲释能并翻转阀门，反转气流形成机械不稳定性驱动的负反馈振荡。
- Mechanism: 低伸长织物腔减少寄生形变，刚性件保证阀位切换；通过梁刚度、特征长度和供压调频。多个 PHO 可闭环级联得到相位错开的步态，附加梁改变两向阈值实现相位偏置。
- Boundary advanced: 它把高频振荡、气动逻辑、任意数量级联和相位调制整合在一个模块中；仍需要持续气源，游泳转向目前靠手动调附加梁而非环境闭环。
- Old problem: 既有带执行能力的气动振荡器最高约 15 Hz，双稳态方案常低于 3.3 Hz，环形振荡器又限制为奇数执行器并难调相。
- Why it works: 9 ms 的双稳态翻转提供快速强机械开关，阀门双稳态避免对置气腔互相抵消，软弹刚耦合让供气能量更有效地转成关节运动。
- True novelty: 它让硬件同时承担高频节律发生器和部分控制器角色，并能以模块化气路产生跳跃、爬行和可转向游泳的不同节律。
- Evidence: PHO 完整周期 19 ms、最高 51 Hz，并经受超过 300 万次循环；仿袋鼠机器人在 100 kPa、28 Hz 下达 68 cm/s，爬行机器人在 100 kPa、4 Hz 下达 0.8 body lengths/s，游泳机器人可在 γ=110° 时以 260 mm 半径转弯超过 180°。
