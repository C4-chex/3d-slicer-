# 3D Slicer 简体中文化：新手照着做就行

这篇教程适合第一次用 3D Slicer 的同学。你不需要懂编程，也不用下载新的软件。

完成后，3D Slicer 的菜单、按钮和大部分功能会变成简体中文。

> 本教程以 Windows 和 3D Slicer 5.12.4 为例。其他版本的按钮名字可能略有不同，但位置相近。

---

## 先说结论：要做哪几件事？

只要完成下面三件事：

1. 打开 3D Slicer；
2. 让它把自带的中文翻译“安装进去”；
3. 在语言列表中选“简体中文”，然后重启软件。

---

## 方法一：推荐，完全用鼠标点（最适合新手）

### 第 1 步：打开 3D Slicer

双击桌面上的 **3D Slicer** 图标。

等到你看到主界面：中间是几块黑色或灰色的医学影像窗口，上方有一排菜单和工具按钮，就表示打开成功了。

### 第 2 步：找到“Language Tools”

在窗口左上角附近，找到一个可以选择模块的下拉框。它通常显示当前模块名称，例如 `Data`、`Welcome to Slicer` 或其他英文。

1. 点击这个模块下拉框；
2. 在弹出的搜索框中输入：`Language Tools`；
3. 点击搜索结果中的 **Language Tools**。

如果你完全搜不到它，请看后面的“常见问题 1”。

### 第 3 步：告诉 Slicer 中文翻译文件在哪

进入 **Language Tools** 后，找到类似下面的选项：

- `Local TS files folder`（本地 TS 翻译文件夹）
- `Local TS folder path`（本地 TS 文件夹路径）

请按下面做：

1. 选择 **Local TS files folder**；
2. 点击路径右侧的文件夹按钮；
3. 打开这个文件夹：

   ```text
   D:\3dslicer\3D Slicer 5.12.4\bin\translations\translations
   ```

4. 点击 **Select Folder / 选择文件夹**。

> 如果你的 Slicer 没装在 `D:\3dslicer`，不要慌：找到你安装 Slicer 的文件夹，继续按 `bin → translations → translations` 一层层打开即可。

### 第 4 步：点击“更新”

在同一个页面找到 **Update** 或 **Apply** 按钮，点击它。

这一步会把翻译文件处理并安装好。等待期间不要关闭 Slicer。

当页面下方的提示框出现类似下面的文字时，说明成功了：

```text
Update completed!
```

或：

```text
Installed ... translation files
```

### 第 5 步：选择简体中文

仍在 **Language Tools** 页面，找到 **Application language**（应用程序语言）或语言下拉框。

1. 点击下拉框；
2. 选择 `Chinese (Simplified)`、`中文（简体）`、`Chinese (China)` 中任意一个；
3. 点击页面上的 **Restart**（重启）按钮。

重启后，3D Slicer 就会以简体中文显示。

---

## 方法二：如果你不想一个个点按钮

这个方法只需要复制一条命令。适合“方法一找不到按钮”或希望一次完成的情况。

### 第 1 步：关闭 3D Slicer

先把所有打开的 3D Slicer 窗口关闭。

### 第 2 步：打开 PowerShell

在 Windows 搜索框输入 `PowerShell`，打开 **Windows PowerShell**。

### 第 3 步：复制下面整段命令，粘贴后按 Enter

```powershell
& 'D:\3dslicer\3D Slicer 5.12.4\Slicer.exe' --no-main-window --python-code "import sys, slicer; sys.path.append(r'D:\3dslicer\3D Slicer 5.12.4\slicer.org\Extensions-34645\LanguagePacks\lib\Slicer-5.12\qt-scripted-modules'); from LanguageTools import LanguageToolsLogic; logic=LanguageToolsLogic(); logic.copyTsFilesFromFolder(r'D:\3dslicer\3D Slicer 5.12.4\bin\translations\translations',False); logic.normalizeTsFiles(); logic.convertTsFilesToQmFiles(); logic.installQmFiles(); logic.installFontFiles(); logic.enableInternationalization(); settings=slicer.app.userSettings(); settings.setValue('language','zh_CN'); settings.setValue('Views/FontFile/SansSerif','NotoSansTC-Regular.otf'); settings.setValue('Views/FontFile/Serif','NotoSerifTC-Regular.otf'); settings.sync(); slicer.app.quit()"
```

执行时窗口看起来像“没反应”也正常；请等待十几秒，直到命令执行结束。

### 第 4 步：正常打开 3D Slicer

再双击 3D Slicer 图标。此时软件应显示中文。

---

## 怎么确认真的成功了？

重新打开软件后，观察顶部菜单和左侧模块名称。

- 看到 `文件`、`编辑`、`视图` 等中文菜单：成功。
- 大部分界面是中文，但某些扩展还是英文：也是正常的。
- 完全没有变化：关闭软件后再打开一次；仍无变化再按“常见问题 2”处理。

---

## 常见问题

### 1. 搜不到 Language Tools，怎么办？

说明还没有安装语言包扩展。

1. 打开 Slicer 顶部菜单中的 **View → Extension Manager**；
2. 搜索 `LanguagePacks`；
3. 点击安装；
4. 按提示重启 Slicer；
5. 回到本教程的“方法一，第 2 步”。

### 2. 选了中文，重启后还是英文，怎么办？

请重新进入 **Language Tools**，确认这两件事：

- 已点击过 `Update`，且出现 `Update completed!`；
- 语言下拉框选择的是 `Chinese (Simplified)` 或 `Chinese (China)`。

然后再次点击 `Restart`。

### 3. 中文显示成方块或乱码，怎么办？

再次运行一次“方法二”。该方法会安装 Noto 中文字体，通常能解决缺字问题。

### 4. 为什么还有一些英文？

3D Slicer 的中文翻译按模块和扩展分别提供。软件核心功能通常能翻译；部分第三方扩展作者没有提供中文翻译时，会继续显示英文，这不影响使用。

---

## 给老师或同学的一句话说明

本方法使用 3D Slicer 的 `LanguagePacks` 扩展，将软件自带的简体中文 `.ts` 翻译文件编译为 Qt 可加载的 `.qm` 文件，安装后在程序设置中启用简体中文界面。
