; Inno Setup 스크립트 (T9.5).
;
; deploy/build.py가 만든 dist/OfflineRAGSearch/ 를 그대로 감싸 설치 마법사를
; 만든다. 관리자 권한이 필요 없도록 설치 경로를 {localappdata}로 잡는다
; (PRD 4장 "관리자 권한 불필요", TECH 9.1).
;
; 빌드:
;   1) python -m deploy.build            (dist/OfflineRAGSearch/ 준비)
;   2) iscc deploy\installer.iss         (Inno Setup Compiler, 별도 설치 필요)
;      결과물은 deploy\output\OfflineRAGSearchSetup.exe

#define MyAppName "오프라인 문서 검색"
#define MyAppVersion "0.1.0"
#define MyAppExeName "OfflineRAGSearch.exe"
#define MyDistDir "..\dist\OfflineRAGSearch"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
; 관리자 권한 없이 사용자 폴더에 설치 — PRD 4장 전제.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=OfflineRAGSearchSetup
; LibreOffice·모델처럼 이미 압축된 바이너리가 큰 비중이라 LZMA 압축
; 효과는 제한적이다(TECH 9.2) — 그래도 코드·텍스트 리소스 몫은 줄어든다.
Compression=lzma2/max
SolidCompression=yes
; 레지스트리를 거의 안 건드린다 — 설치 경로·제거 등록 정보 정도만
; Inno Setup이 기본으로 남기는 항목이고, 앱 자신의 동작(모델/인덱스/캐시
; 경로)은 전부 설치 폴더 기준 상대 경로다(T9.3 검증 대상).
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
; 전체 폴더를 그대로 담는다 — 하위 구조(models/·vendor/·font/)를 규칙 없이
; recursesubdirs로 통째로 옮기는 게, 폴더가 늘어날 때마다 이 스크립트를
; 고쳐야 하는 개별 나열보다 안전하다(TECH 9.1 "포터블 구조" 원칙과도 맞음
; — 배포 폴더 구조 자체가 이미 "복사하면 끝"이어야 한다).
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; data/(사용자 인덱스·설정)는 프로그램 자체가 만드는 폴더라 Inno Setup의
; 파일 목록에 없다 — 제거 시 같이 지우지 않으면 남는다. 사용자 데이터를
; 조용히 지우는 것도 위험하므로, 제거 안내에 수동 삭제 경로만 알려주는
; 편이 안전하다(아래 [Code] 참고).

[Code]
procedure DeinitializeUninstall();
begin
  MsgBox('검색 인덱스와 설정은 이 프로그램을 지워도 남아 있습니다.' + #13#10 +
    '완전히 지우려면 다음 폴더를 직접 삭제하세요:' + #13#10 +
    ExpandConstant('{app}\data'), mbInformation, MB_OK);
end;
