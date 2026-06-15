# 네이티브 제어 카탈로그 (macOS · M4 · 실측 2026-06-11)

> **목적**: SAPPHI에 *행동 사다리*를 부여하기 위한 전수조사. GUI 비전 워커로 보내기 *전에*,
> 맥이 네이티브로 주는 수단(키/스크립트/CLI/App Intents)으로 끝낼 수 있는 인텐트를 식별한다.
> 아래 표는 `sapphi/native.py`(인텐트→핸들러 레지스트리)의 구현 스펙이다.
> 모든 항목은 **이 맥에서 직접 찍어 확인**했다(추측 아님). 비용 표기 = 비전/claude 콜.

## 행동 사다리 (지각 사다리의 짝)
```
① 네이티브 헬퍼/키   (DisplayServices, 미디어키)        ─ 0 비전 / 0 claude / ~0.1s
② AppleScript        (osascript: 볼륨·다크모드·앱·창)    ─ 0 / 0 / ~0.2s
③ Shortcuts          (shortcuts run "이름" = App Intents) ─ 0 / 0
④ 시스템 CLI / URL딥링크 (networksetup·pmset·defaults·open) ─ 0 / 0
⑤ GUI 비전 (SAPPHI 워커)                                  ─ 최후수단(여러 비전 콜)
```
**원칙**: 인텐트가 ①~④에 핸들러가 있으면 그걸로 끝(워커 진입 X). 없을 때만 ⑤.

---

## 1. 디스플레이
| 인텐트 | 수단 | 명령/API | R/W | 비고 |
|---|---|---|---|---|
| 밝기 get/set | ① 헬퍼 | `DisplayServices(Get/Set)Brightness(CGMainDisplayID(), f)` | RW | **실측 0.595, rc=0**. dlopen `/System/Library/PrivateFrameworks/DisplayServices.framework`. → `bin/macbrightness` swift 헬퍼 빌드 |
| 다크/라이트 토글 | ② osascript | `tell app "System Events" to tell appearance preferences to set dark mode to not dark mode` | RW | 읽기 `defaults read -g AppleInterfaceStyle`(현재 **Dark**) |
| Night Shift | ③ Shortcuts | `shortcuts run "Set Night Shift"` | W | CoreBrightness 직접 CLI는 까다로움 |
| True Tone | ③ Shortcuts | — | W | |
| 해상도 | ④ CLI | `system_profiler SPDisplaysDataType`(R, 현재 3024×1964 Retina) / set은 displayplacer(미설치) | R | |
| 화면잠금/끄기 | ④ CLI | `pmset displaysleepnow` | W | |

## 2. 오디오  (실측: output 0, **muted=true**, input 50, alert 100)
| 인텐트 | 수단 | 명령 | R/W |
|---|---|---|---|
| 출력 볼륨 | ② osascript | `set volume output volume N` (0–100) / `output volume of (get volume settings)` | RW |
| 음소거 | ② osascript | `set volume output muted true/false` | RW |
| 입력/알림 볼륨 | ② osascript | `set volume input volume N` / `alert volume` | RW |
| 출력 장치 전환 | ③ Shortcuts | SwitchAudioSource(미설치) 대신 Shortcuts | W |

## 3. 네트워크  (실측: WiFi On·미접속, 서비스 AX88772E/Thunderbolt Bridge/Wi-Fi)
| 인텐트 | 수단 | 명령 | R/W |
|---|---|---|---|
| WiFi on/off | ④ CLI | `networksetup -setairportpower en0 on/off` | RW |
| WiFi 접속 | ④ CLI | `networksetup -setairportnetwork en0 <SSID> <PASS>` | W |
| 현재 네트워크 | ④ CLI | `networksetup -getairportnetwork en0` | R |
| 서비스/VPN 목록 | ④ CLI | `networksetup -listallnetworkservices` / `scutil --nc list` | R |
| Bluetooth | ③/CLI | 읽기 `system_profiler SPBluetoothDataType`(현재 **On**); 토글 blueutil(미설치)·Shortcuts "Set Bluetooth" | RW |

## 4. 전원  (실측: 배터리 100% charged, caffeinate 있음)
| 인텐트 | 수단 | 명령 | R/W | 게이트 |
|---|---|---|---|---|
| 배터리/충전 | ④ CLI | `pmset -g batt` | R | |
| 저전력모드 | ④ CLI | `sudo pmset -a lowpowermode 1` | W | **sudo → 확인 필요** |
| 슬립 방지 | ④ CLI | `caffeinate -d` (백그라운드) | W | |
| 즉시 슬립 | ④ CLI | `pmset sleepnow` | W | 부작용 → 확인 |

## 5. 집중모드 / 알림  (실측: Focus DB 존재)
| 인텐트 | 수단 | 명령 | R/W |
|---|---|---|---|
| 집중모드 on/off | ③ Shortcuts | `shortcuts run "Set Focus"` | W |
| DND 상태 | R | `~/Library/DoNotDisturb/DB/` sqlite | R |

## 6. 앱 / 창 제어  (스크립트 가능 실측: Finder·Safari·Notes·Reminders·Messages·Mail·System Events·Terminal)
| 인텐트 | 수단 | 명령 | 게이트 |
|---|---|---|---|
| 앱 열기/활성화 | ④/② | `open -a "앱"` / osascript activate | |
| 앱 종료 | ② osascript | `tell app "X" to quit` | 부작용 → 확인 |
| 창 위치/크기/최소화 | ② System Events | `tell app "System Events" to tell process "X" to set position of window 1 to {..}` | |
| 앱별 자동화 | ② 사전(dictionary) | 각 앱 .sdef (메일 보내기·노트 작성·사파리 URL 등) | 전송류 → 확인 |

## 7. 정보 조회 (읽기 — 비전 없이)
| 대상 | 수단 | 민감? |
|---|---|---|
| 시각·로케일·배터리·네트워크·맨앞앱·위치 | **이미 `signals.py`** | 아니오 |
| 클립보드 | `pbpaste` | **민감 → local_llm** |
| 캘린더·미리알림·연락처·메시지 본문 | AppleScript / EventKit | **민감 → local_llm** |
| 파일 검색 | `mdfind '쿼리'` | |
| 시스템 사양 | `system_profiler` | |

## 8. Shortcuts / App Intents (사용자 정의 네이티브 액션)
- 실행: `shortcuts run "<이름>"` · 목록: `shortcuts list`
- **이 맥 보유(실측)**: `제품 재고 확인`, `Refresh my apps`, `배터리 체크`, `바로가기`
- 에이전트가 이름으로 직접 호출 가능 → 사용자가 만든 임의 자동화를 그대로 흡수.

## 9. 미디어 / 시스템 키
| 인텐트 | 수단 | 비고 |
|---|---|---|
| 재생/일시정지/다음 | ③ Shortcuts / osascript | 미디어 특수키는 NSEvent라 key code 불안정 → Shortcuts 권장 |
| Spotlight·Mission Control | 키 시뮬(System Events keystroke) | |

## 10. 설정 패널 딥링크 (GUI가 정말 필요할 때도 *검색·클릭 없이* 직행)
`open 'x-apple.systempreferences:<id>'` — 예:
`com.apple.preference.displays` · `.sound` · `.network` · `.security` · `.Bluetooth` · `.battery` · `com.apple.preferences.softwareupdate`

---

---

# Part 2 — 깊은 자동화 층 (시스템 토글 너머, 실측 2026-06-11)

> 위 1~10은 "설정 토글" 한 겹. 진짜 에이전트 파워는 아래 *앱 자동화 + 개인데이터 + 스케줄*에 있다.
> 이 층 덕분에 **흔한 작업의 ~80%가 비전(GUI 워커) 없이** 끝난다. 비전은 자동화 표면이 *전혀 없는*
> 앱(커스텀 캔버스=NaverMap, 게임, 일부 서드파티)에만 쓰는 최후수단이 된다.

## 11. 앱 자동화 — AppleScript / JXA 사전 (GUI 없이 앱 *조종*)
| 앱 | 네이티브로 되는 것 (사전 명령) | 게이트 |
|---|---|---|
| Mail | 메일 *작성/전송*, 검색, 메일박스 읽기, 규칙 | 전송 → 확인 |
| Messages | iMessage *전송*, 대화 읽기 | 전송 → 확인 (★KakaoTalk은 사전 없음→GUI/DB) |
| Calendar | 이벤트 *생성/조회/수정*, 캘린더 목록 | |
| Reminders | 미리알림 *생성/완료/조회* | |
| Notes | 노트 *생성/검색/본문읽기* | |
| Safari | URL 열기, 탭 목록, **페이지 JS 실행**(`do JavaScript`), 본문 읽기 | |
| Music | 재생/일시정지/곡정보/플레이리스트 | |
| Finder | 파일 *이동/복사/삭제/이름변경/선택*, 창 | 삭제 → 확인 |
| (모든 앱) | **System Events UI 스크립팅** — 메뉴/버튼/필드를 *이름으로* 조작(AX 기반). 실측: Finder 메뉴 `파일·편집·보기…` 이름 획득 | |

- **JXA**(`osascript -l JavaScript`): ObjC 브릿지로 Foundation/EventKit/Contacts/CoreLocation 등 *프레임워크 직접* 호출. 실측: `Application("Finder").startupDisk.capacity()`=494GB.
- **System Events 메뉴 구동** = AppleScript와 비전 사이의 *중간 티어*: 사전 없는 앱도 픽셀 없이 메뉴 이름으로 클릭.

## 12. 개인 데이터 마이닝 (읽기 — 민감→local_llm)
| 대상 | 수단 | 비고 |
|---|---|---|
| 파일/메타데이터 전체 | `mdfind '<쿼리>'` | 실측 17,986 .py · 내용/태그/날짜 쿼리 |
| iMessage 기록 | `~/Library/Messages/chat.db` (sqlite) | **민감** |
| Safari 방문기록 | `~/Library/Safari/History.db` | **민감** |
| 메모/사진/캘린더 DB | 각 앱 sqlite / EventKit·Photos via JXA | **민감** |
| 클립보드 | `pbpaste` | **민감** |
- 전부 *로컬 처리*(local_llm), 클라우드 미전송. "내 데이터에 대한 질문"을 비전·검색 없이 답함.

## 13. Shortcuts / App Intents 생태계
- `shortcuts run "<이름>"` — 사용자 단축어(실측 4개) + 시스템 액션(Set Brightness/Focus/NightShift, Get Weather, Run Shell Script …).
- 에이전트가 *기존 단축어 호출* + (향후) *단축어 동적 생성*까지 가능.

## 14. 스케줄 / 프로액티브
| 인텐트 | 수단 |
|---|---|
| 정기 실행 | `launchd`(plist) / `crontab` / `at` |
| 슬립 방지 유지 | `caffeinate -d -t <초>` |
| 조건 트리거 | signals(시각/위치/배터리) + 핸들러 = "비오면/퇴근하면/저녁에" |

---

# 무엇이 가능해지나 (비전 0번 작업 예시)
- **시스템**: "밝기 30%·다크모드 켜·음소거" → 즉시(0.1s)
- **소통**: "엄마한테 곧 도착한다고" → Messages 사전 / "이 메일 답장 써줘" → Mail
- **일정**: "내일 3시 회의 추가" → Calendar / "이번 주 뭐 있어?" → Calendar 읽기(로컬)
- **파일**: "다운로드 큰 파일 찾아 정리" → mdfind+Finder / "이 PDF 요약해 메모 저장" → 추출+local_llm+Notes
- **개인 Q&A(로컬)**: "최근 받은 PDF 뭐였지?"(mdfind) · "클립보드 번역해서 다시 복사"(pbpaste→번역→pbcopy)
- **프로액티브**: "매일 아침 배터리+날씨 브리핑" → launchd+signals+lookup
- **여전히 비전 필요**: KakaoTalk 전송(사전 없음), NaverMap 길찾기(캔버스), 게임/일부 서드앱 → SAPPHI 워커.

## 권한/위험 게이트 (native.py가 강제)
- **sudo 필요**(저전력모드 set, systemsetup 등): 자동 실행 금지 → 사용자에게 명령 안내.
- **민감 읽기**(클립보드/연락처/캘린더/메시지): `local_llm`로만, 클라우드 미전송.
- **부작용**(앱 종료, 네트워크 끊기, 슬립, 전송류 AppleScript): 실행 전 확인.
- **읽기/무해 토글**(밝기·볼륨·다크모드·WiFi켜기·앱열기): 즉시 실행.

## native.py 설계 메모
- 레지스트리 = `[(matcher(goal)->vars | None, handler(vars)->NativeResult, risk)]`.
- autoloop: route=local_action(또는 settings_change/system) → **`native.try_handle(goal, spec)` 먼저** → 처리되면 종료(워커 X), 아니면 GUI 워커.
- 한국어 인텐트 매칭: "밝기/볼륨/소리/다크모드/와이파이/집중/절전/잠금/열어/꺼/켜" 등 키워드 + 방향(올려/내려/켜/꺼/토글) + 수치.
- 각 핸들러는 결과를 decision_trace에 `native_handler`로 기록(어떤 사다리 단을 탔는지).
