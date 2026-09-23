# VAM-s-TSC-to-OFS-3D-Simulator
这是一个方便VAM创作者的插件，用处是：在创作VAM场景时，可以时刻模拟SR6的运动状态，以便于进行调整，就像用OpenFunscript写脚本一样方便。

大家好，我是AstraeaG，这是我第一个创建在Github的项目。

这是一个方便VAM创作者的插件，用处是：在创作VAM场景时，可以时刻模拟SR6的运动状态，以便于进行调整，就像用OpenFunscript写脚本一样方便。
传统在创作VAM场景时需要在真实世界时刻连接着SR6来观察机器的运动状态，非常的不方便。本程序可以在电脑内部时刻模拟SR6的运动状态，大大减少外部繁琐的工作。
原理是：利用VAM内的插件ToySereialController输出的串口数据流，通过技术手段将其到用于OpenFunscript的3D模拟器插件（该模拟器恰好也是一个独立的软件）。

以下是使用流程：
第一步：创建一对虚拟COM口（比如COM7和COM8），我利用的软件是Configure Virtual Serial Port Driver（VSPD）
<img width="1212" height="796" alt="image" src="https://github.com/user-attachments/assets/62a2a29f-ddee-4274-99a7-af19808fb4a6" />

第二步：用串口助手等工具测试创建出来的虚拟COM口是否能正常互发消息。
<img width="3570" height="1350" alt="image" src="https://github.com/user-attachments/assets/fa3f8897-4424-4447-b5f5-5db5b6e452e3" />

第三步：下载FunscriptSimulator3D.exe，下载地址：https://github.com/OppositeOdd/OFS_Simulator3D

第四步：打开 SerialToOFS.exe，首次会自动检测同目录的 FunscriptSimulator3D.exe。
<img width="864" height="234" alt="image" src="https://github.com/user-attachments/assets/2f31e757-5bec-4f20-a333-ec67ba64244b" />

第五步：配置参数，如：
    1.串口号（比如，我创建的是COM7和COM8，我选择COM8）
    2.波特率（默认115200）
    3.WebSocket 地址（本地地址：ws://127.0.0.1:8080/ofs）
    4.点“应用配置”，程序会自动更新配置文件config。
    <img width="756" height="624" alt="image" src="https://github.com/user-attachments/assets/b66a57cb-25b0-492f-a055-96bafc049e9d" />

第六步：配置VAM和ToySereialController插件,如：
    1.启动VAM,进入一个可用场景，
    2.在ToySereialController插件中配置连接方式为：Serial
    3.输出COM口选择COM7（COM7和COM8是一对可相互发送数据的虚拟串口）
    4.开始游玩场景，并观察右方的3D模拟器是否有动作。
    <img width="2654" height="1402" alt="image" src="https://github.com/user-attachments/assets/8fdbdc17-0695-4eaa-8d0b-e57e75ed447d" />


FAQ:
1.如果VAM内ToySereialController插件的设置界面是空白？ 
    请重启VAM，重新进入场景。
2.如果ToySereialController内找不到想要的COM口？ 
    请检查你创建的虚拟COM口是否能正常使用，可用串口助手等工具测试。
3.如果配置好插件和参数后，在创作时3D模拟器没有运动？ 
    请检查SerialToOFS中配置的WebSocket地址及端口是否与3D模拟器的一致，更改参数设置后点击“应用”后重启SerialToOFS软件。

如果这软件帮到你了，请点击星星或留下评论。
有问题请发Issue或邮箱联系：astraeag@qq.com，有空我都会解答的。

感谢以下开源项目，排名不分先后：
1.https://github.com/OppositeOdd/OFS_Simulator3D
2.https://github.com/OpenFunscripter/OFS
3.DeepSeek Web
