; PDF Reader —— NSIS 安装程序脚本（Modern UI 2）
; 由 scripts/build_windows.py --installer 用缓存的 makensis 编译。
; 运行目录约定为项目根，故下列默认路径为相对根目录的相对路径。
; 产物：dist/PDFReader-Setup-<version>.exe（正规向导式安装程序，非 zip）

Unicode true

!include "MUI2.nsh"
!include "FileFunc.nsh"
!insertmacro GetSize

; -------- 可由命令行 /D 覆盖 --------
!ifndef VERSION
  !define VERSION "0.1.0"
!endif
!ifndef SOURCEDIR
  !define SOURCEDIR "dist\PDFReader"
!endif
!ifndef OUTFILE
  !define OUTFILE "dist\PDFReader-Setup-${VERSION}.exe"
!endif
!ifndef ICON
  !define ICON "src\pdfreader\resources\icons\app.ico"
!endif

!define APPNAME "PDF Reader"
!define COMPANY "PDF Reader Contributors"
!define EXENAME "PDFReader.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFReader"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\${APPNAME}"
InstallDirRegKey HKLM "Software\${APPNAME}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

; 版本信息（右键属性可见）
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "CompanyName" "${COMPANY}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APPNAME} Setup"
VIAddVersionKey /LANG=2052 "LegalCopyright" "AGPL-3.0"

; -------- 界面 --------
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXENAME}"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 ${APPNAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; 简体中文优先，英文备选
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; -------- 安装 --------
Section "install"
  SetOutPath "$INSTDIR"
  File /r "${SOURCEDIR}\*"

  ; 快捷方式
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXENAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"

  ; 卸载器 + 注册表（应用和功能）
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\${APPNAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXENAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1

  ; 注册为 PDF 的「打开方式」候选（不抢默认程序）：资源管理器右键「打开方式」与
  ; 双击时的选择列表里会出现本程序，程序从命令行参数接收文件路径
  WriteRegStr HKLM "Software\Classes\PDFReader.Document" "" "PDF 文档"
  WriteRegStr HKLM "Software\Classes\PDFReader.Document\DefaultIcon" "" "$INSTDIR\${EXENAME},0"
  WriteRegStr HKLM "Software\Classes\PDFReader.Document\shell\open\command" "" '"$INSTDIR\${EXENAME}" "%1"'
  WriteRegStr HKLM "Software\Classes\.pdf\OpenWithProgids" "PDFReader.Document" ""
  WriteRegStr HKLM "Software\Classes\Applications\${EXENAME}\shell\open\command" "" '"$INSTDIR\${EXENAME}" "%1"'
  WriteRegStr HKLM "Software\Classes\Applications\${EXENAME}\SupportedTypes" ".pdf" ""
  ; 通知资源管理器文件关联已变化
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

  ; 估算体积（KB）供“应用和功能”显示
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" "$0"
SectionEnd

; -------- 卸载 --------
Section "Uninstall"
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\${APPNAME}"
  DeleteRegValue HKLM "Software\Classes\.pdf\OpenWithProgids" "PDFReader.Document"
  DeleteRegKey HKLM "Software\Classes\PDFReader.Document"
  DeleteRegKey HKLM "Software\Classes\Applications\${EXENAME}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd
