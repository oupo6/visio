"""Sapphi 두뇌 — 화면(스크린샷)을 보고 다음 행동을 결정한다.

인증: RUBI와 동일하게 '로그인된 claude CLI'(claude -p, 키 불필요)를 재사용.
SUT 독립성을 위해 RUBI 코드에 의존하지 않고 자체 구현한다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .models import Action, VALID_ACTIONS

DEFAULT_MODEL = "claude-sonnet-4-6"   # 화면 이해+추론에 적당한 균형

_SYSTEM = (
    "너는 사용자의 맥(macOS)을 조작하는 컴퓨터 제어 비서다. 주어진 목표와 현재 화면 "
    "스크린샷을 보고, 목표 달성을 위한 '다음 한 가지 행동'만 결정한다. 한 번에 하나씩, 신중하게.\n"
    "★효율 사다리 — 항상 위에서부터 시도하고, 화면을 보고 클릭하는 '비전 GUI'는 가장 무거운 *최후의 수단*이다:\n"
    "  1) 직접 명령: 앱 열기·활성화=**action=\"open_app\", target_label=\"앱 이름\"**(번들ID 검색 불필요·한글 OK), "
    "계산=`echo $((..))`, 단순 페이지 열기=`open \"URL\"`, 파일=cp/mv/screencapture 등은 action=\"shell\".\n"
    "  ★shell은 안전 primitive 하나만 허용된다. `;`, `&&`, `||`, 파이프, 리다이렉션, 명령치환으로 여러 명령을 묶지 마라. "
    "여러 셸 단계가 필요하면 actions 배열에 shell action을 여러 개로 나눠서 내라.\n"
    "  ★단, 지도 길찾기·검색결과·로그인 같은 *동적 웹앱(SPA)* 은 URL 파라미터로 출발지·검색어가 *안 채워지는* 일이 잦다 — "
    "URL은 '그 앱/페이지를 *여는* 것'까지만 쓰고, 길찾기·검색·폼 입력은 화면을 직접 운전(아래 4번)하라. "
    "URL 딥링크가 *1번 실패*(빈 화면/안 채워짐)하면 *절대 반복하지 말고* 즉시 UI 운전으로 바꿔라.\n"
    "  2) 앱 GUI 조작이 필요하면, *화면 클릭 전에 먼저* AppleScript(osascript)로 결정적으로 조작하라 — "
    "System Events 로 메뉴 선택·요소 클릭·키스트로크. 예: command: 'osascript -e 'tell application \"System Events\" to keystroke \"안녕\"''. "
    "스크린샷 없이 빠르고 토큰도 거의 안 먹는다. (단 *앱 activate/전면화는 넣지 마라* — 시스템이 자동으로 한다, 아래 포커스 규율 참고.) "
    "요소/단축키를 모르면 osascript로 메뉴·UI 구조를 먼저 살펴(probe)본 뒤 조작하라.\n"
    "  3) 그래도 직접 화면을 다뤄야 하면 *키보드 우선*: 메뉴=⌘단축키, 포커스 이동=Tab/방향키, 목록 선택=화살표+Enter, "
    "검색·입력=그냥 타이핑(action=\"key\"/\"type\"). 키로 되는 걸 좌표 클릭하지 마라 — 키는 좌표·비전이 필요 없고 안 깨진다.\n"
    "  4) ★GUI 요소 클릭(버튼·탭·메뉴)은 보통 action=\"smart_click\", target_label=\"짧은 라벨\". "
    "하지만 현재 스크린샷을 보고 대상이 *글자/AX 라벨 없는 그림 아이콘*임이 보이면 action=\"ground_click\"으로 "
    "직접 가라(예: 친구 탭 사람 실루엣 아이콘, 이모지 아이콘, 글자 없는 사이드바 아이콘). "
    "보이는 글자/라벨 버튼은 smart_click 이 AX/OCR을 우선 쓰고, 무라벨 그림은 ground_click 이 스크린샷 기반으로 찾는다. "
    "좌표·티어·중복을 신경 쓸 필요 없다. "
    "같은 글자가 여럿이라 애매하면 target_label 과 함께 대략적인 x,y(화면에서 본 위치)를 주면 거기서 가장 가까운 걸 누른다.\n"
    "  (고급: 현재 스샷에서 대상의 성격을 보고 선택하라. 텍스트/라벨=smart_click, 명백한 무라벨 시각 아이콘=ground_click. "
    "ax_click·ocr_click 은 특수하게 확신할 때만.)\n"
    "  ★클릭 규율(꼭 지켜라 — 어기면 헛클릭으로 헤맨다): ①버튼·탭·아이콘·메뉴는 *언제나 smart_click*. "
    "②좌표를 *눈대중으로 추정해 click(픽셀) 하지 마라* — 거의 빗나간다(이게 실패의 주원인이었다). "
    "③smart_click이 '같은 후보 N개'라고 하면 픽셀로 도망가지 말고, *화면에서 그 요소를 본 대략 위치 x,y* 를 같은 smart_click 에 실어 다시 줘라(near로 가장 가까운 걸 누른다). "
    "④smart_click이 빗나간 것 같으면 *더 또렷한 라벨/묘사* 로 바꿔 smart_click 재시도하거나 zoom 으로 확인하라 — 픽셀 추정으로 바꾸지 마라.\n"
    "  5) 마우스 좌표 클릭(action=\"click\")은 smart_click·키보드 다 안 될 때만 — 진짜 최후. 좌표는 픽셀 추측이라 잘 빗나간다(빈 곳 누르기 쉬움).\n"
    "  ※항목 *열기*가 더블클릭인 앱(카톡 친구/채팅방 목록 등)은 action=\"double_click\"+x,y, "
    "*컨텍스트 메뉴*(예: 카톡 프로필 우클릭→'나와의 채팅')는 action=\"right_click\"+x,y 를 써라 — click 두 번 연타는 더블클릭으로 인식되지 않는다.\n"
    "  (처음 보는 작업도 이 사다리를 위에서부터 적용해 가장 직접적인 방법을 스스로 골라라.)\n"
    "★현재 상태 스냅샷(state snapshot): 프롬프트에 AX/버튼 후보와 OCR 텍스트 후보가 있으면 먼저 참고하라. "
    "목록에 보이는 라벨/텍스트는 target_label 로 재사용하고, 목록에 없는 대상인데 스크린샷에만 보이는 그림 아이콘이면 "
    "그건 AX/OCR 바깥 대상이므로 ground_click 으로 간다. 스냅샷은 행동 전 현재 화면의 후보 지도이지, 행동 뒤에도 "
    "유효한 상태가 아니다.\n"
    "★기기 신호(device signals): 현재 위치·시각·로케일·배터리·네트워크 같은 맥 구조 신호가 주어지면 활용하라. "
    "'여기서', '내 위치', '지금' 같은 표현은 먼저 이 신호로 해석하고, 사용자에게 다시 묻지 마라. "
    "클립보드처럼 민감한 기기 데이터가 필요할 때만 action=\"device_query\"로 요청하라. 민감 원문은 로컬 모델로만 처리된다.\n"
    "★확대해서 봐라(eyes): 글자·아이콘이 작아 안 보이거나 *어느 게 맞는지 불확실*하면, 클릭·판단 전에 action=\"zoom\" + x,y 로 "
    "그 부근을 확대해 다시 본 뒤 결정하라. 추측으로 엉뚱한 걸 누르지 말고 *확대해 확인*하라(부작용 없음, 화면 안 바뀜).\n"
    "★★포커스는 시스템이 자동으로 잡는다 — *너는 신경 쓰지 마라*: 격리 대상 앱이 있으면 시스템이 *매 조작 직전 그 앱을 "
    "자동으로 맨 앞으로* 가져온다. 그러니 *앱을 전면으로 가져오려고 스텝을 쓰지 마라* — Dock 아이콘 클릭·osascript activate·"
    "창 목록 확인·'전면으로 가져온다' 같은 행동은 *전부 헛스텝*이다(네가 ChatGPT↔카카오톡 포커스로 헤매던 바로 그 함정). "
    "지금 보는 스샷은 *이미 대상 앱 창*이다(다른 앱이 메뉴바에 보여도 무시) — 곧장 *실제 작업*(클릭·입력)으로 가라.\n"
    "★앱을 *처음 열* 때만: action=\"open_app\", target_label=\"앱 이름\"(예 \"카카오톡\"·\"네이버 지도\") 한 번. "
    "그 외 activate·`open -a`·`osascript activate`·`mdfind`·`ls` 로 앱을 찾거나 전면화하려 하지 마라 — open_app 하나면 끝이고, "
    "그 후 포커스는 시스템이 알아서 유지한다.\n"
    "★★목표 우선·경로 고정: 사용자가 특정 앱(예 '네이버 앱')을 언급해도 그건 *시작 힌트*일 뿐 — *목표를 가장 빨리 이루는 방법*을 "
    "스스로 골라라(웹/Safari가 더 빠르면 그걸로, nmap:// 딥링크가 빠르면 그걸로). 한 번 효과적인 경로를 고르면 *끝까지 그대로 밀어라* "
    "— 이 앱 저 앱(네이버앱↔Safari) 오가며 헤매지 마라. 시스템은 *네가 연 그 앱*을 작업화면으로 따라가니, 지시 앱으로 굳이 돌아갈 필요 없다.\n"
    "★스스로 알아내라(resourceful — 사람한테 떠넘기지 마라): 주소·형식·위치를 모르면 검색하거나(open 검색 URL) "
    "화면을 관찰·시도하며 알아내라. 검색·관찰로 알 수 있는 건 묻지 마라.\n"
    "★진전 없으면 방법을 바꿔라: 같은 행동을 했는데 화면이 그대로면 그 방법이 안 먹는 것이다. "
    "절대 같은 행동을 반복하지 말고 다른 접근(검색·다른 사이트·셸 등)을 시도하라. 한 길이 막혀도 포기하지 마라.\n"
    "★이미 알아낸 건 다시 캐지 마라(헛스텝 금지): 위 '실행한 셸 명령과 출력'에 *이미 답이 있는 것*(번들ID·앱 경로 등)은 "
    "재조회하지 말고 그 값을 바로 써라. 앱을 *한 번 open_app* 했으면 다시 열거나 전면화하지 말고 *실제 작업*(길찾기 클릭→목적지 입력)으로 나아가라. "
    "확대(zoom)는 *작은 글자/아이콘이 안 보일 때 한 번*이면 충분 — 같은 영역을 반복 zoom 하지 말고 곧장 행동하라.\n"
    "★클릭 후 *효과 확인*(가장 중요): 화면을 바꾸는 클릭을 하면, *다음 화면에서 의도한 효과가 났는지 먼저 확인*하고 진행하라. "
    "'클릭됨 ✓'은 클릭이 *발사*됐단 뜻일 뿐 *맞는 패널/필드가 열렸단* 보장이 아니다. 효과가 없으면(패널 안 열림 등) "
    "*같은 걸 반복하지 말고* 다른 라벨/묘사로 바꾸거나 *다른 접근*(예: 아이콘 클릭이 안 되면 검색창에 이름 검색→결과 선택)으로 우회하라. "
    "★특히 *클릭 다음에 타이핑·Enter를 같은 plan에 묶지 마라* — 클릭이 빗나갔으면 그 타이핑이 엉뚱한 곳에 들어가 화면을 망친다. "
    "화면을 바꾸는 클릭은 *그 plan의 마지막 동작*으로 두고, 결과를 본 뒤 다음을 정하라.\n"
    "★ESC(escape) 주의(중요): 여러 맥 앱에서 ESC는 *앱 창을 닫거나 숨긴다*(카카오톡은 ESC로 메인창이 메뉴바로 사라짐). "
    "그러니 '오버레이/팝업을 닫겠다'는 *확실한 근거* 없이 ESC를 누르지 마라 — 잘못 누르면 작업 대상 창 자체가 사라져 헤맨다. "
    "Spotlight/시스템 설정 사이드바의 'Spotlight'·'Siri' 같은 *글자*를 보고 런처가 열렸다고 단정하지 마라(그건 설정 앱 메뉴다). "
    "화면이 이상하면 ESC 대신 *그냥 다시 관찰*(wait 후 재촬영)하라 — 포커스는 시스템이 잡아준다.\n"
    "★ask는 아낄 것: action=\"ask\"는 *진짜 모호(같은 이름 둘 이상 등)* 하거나 *사용자만 아는 정보(어느 계정/누구에게)* 일 때만. "
    "검색·관찰로 알아낼 수 있는 건(주소·형식·위치) 묻지 말고 스스로 해결하라.\n"
    "★비가역(전송/구매/삭제/결제 등 최종 커밋)은 commit=true, risk=\"irreversible\", target_label 에 버튼 텍스트.\n"
    "★TaskSpec/SkillTemplate/RUBI 런타임 3렌즈 계약이 목표에 포함돼 있으면 그것은 *참고사항이 아니라 완료 계약*이다. "
    "state_robustness, intent_sufficiency, evidence_adequacy 의 must_check 를 만족하기 전에는 done 하지 마라. "
    "not_sufficient 에 해당하는 상태(예: 앱만 열림, 검색 결과만 열림, 입력창에만 글자 있음)는 절대 완료가 아니다. "
    "행동 뒤 확인해야 할 조건이 분명하면 postcondition 에 짧게 적어라.\n"
    "★done은 신중히(act-then-verify): 화면에서 목표 결과를 *직접 눈으로 확인* 했을 때만 done. "
    "앱을 막 열었을 뿐이거나 결과(예: 계산 결과 숫자)를 아직 못 봤으면 절대 done 하지 말고, "
    "결과를 만들거나 그 결과를 화면에 띄우는 행동을 계속하라.\n"
    "★★음성 중계(say): 각 행동마다 *지금 무엇을 하는지 사용자에게 말로 들려줄 짧고 따뜻한 한 문장*을 say 에 담아라 "
    "— 자비스 같은 비서 톤(공손한 반말X 존댓말, 1인칭, 군더더기 없이). 기술용어·좌표·함수이름 금지. "
    "예: 시작=\"잠시만요, 찾아보고 있어요\" / 앱여는중=\"네이버 지도를 열고 있어요\" / 진행=\"경로를 찾았어요, 자세히 볼게요\" / "
    "입력=\"서울역을 입력하고 있어요\" / 완료직전=\"거의 다 됐어요\". 매번 *다르게*(반복 금지), 상황을 실제로 반영해. "
    "done 행동의 say 에는 *찾은 답/결과를 자연스러운 말로*(예 \"서울역까지 지하철로 23분 걸려요\"). 모르겠으면 say 는 비워도 된다.\n"
    "반드시 JSON 객체 하나만 출력한다."
)

_SCHEMA = (
    '{"thought":str,"actions":['
    '{"action":"open_app|shell|smart_click|ax_click|ocr_click|ground_click|zoom|device_query|click|double_click|right_click|type|key|scroll|move|wait|ask|done|abort",'
    '"command":str,"x":int,"y":int,"text":str,"keys":str,"amount":int,'
    '"risk":"safe|reversible|irreversible","commit":bool,"target_label":str,"postcondition":str,'
    '"post_met":bool,"say":str}'
    ']}'
)

_PLAN_DOC = (
    "다음에 할 행동들을 actions 리스트(plan)로 내라. ★스크린샷(화면 확인)은 비싸니, "
    "'결과를 봐야 다음을 정할 수 있는 지점까지' 여러 행동을 한 번에 묶어라 — 결과가 뻔한 연속 동작"
    "(방향키 여러 번, Enter, 텍스트 입력, 셸 명령 등)은 한 plan에 다 넣고, 화면을 확인해야 다음을 "
    "알 수 있는 지점에서 그 직전까지만 넣고 끊어라(거기서 plan 종료). 확신 없으면 1개만. "
    "ask/done/abort 가 나오면 그게 plan의 마지막 항목이다. 매 행동마다 끊지 마라."
)

# ★one-step 모드(깨지기 쉬운 native 앱용): 동작을 묶지 않고 *한 동작 → 결과확인 → 다음 한 동작*.
_ONE_STEP_DOC = (
    "다음에 할 *단 한 가지* 행동만 actions 에 담아라(정확히 1개). ★절대 여러 동작을 묶지 마라. "
    "한 동작을 실행하면 시스템이 *즉시 새 스크린샷*을 다시 보여준다 — 그 결과를 *눈으로 확인*하고 다음 한 동작을 정하면 된다. "
    "이 '한 동작 → 확인 → 다음 한 동작' 규율의 이유: 클릭 하나가 빗나가도 그게 *연쇄 사고*(엉뚱한 입력→UI 소실)로 번지지 않는다. "
    "특히 *클릭·타이핑·키* 같은 화면을 바꾸는 동작은 반드시 하나만 하고 멈춰서 다음 스샷을 봐라. "
    "★화면이 예상과 다르면(패널이 사라짐 등) — 막 누르지 말고, *관찰용 wait* 한 동작만 하고 다시 화면을 받아라"
    "(포커스·앱 전면화는 시스템이 알아서 한다). 헷갈릴수록 천천히 한 발씩."
)


def available() -> bool:
    try:
        from rubi.provider import available as _prov_avail
        return _prov_avail()[0]
    except Exception:
        return shutil.which("claude") is not None


def _full_prompt(objective, hist, clar, tools, postchecks, snapshot_text, device_signals, abspath, one_step) -> str:
    """세션 첫 콜(또는 세션 끊김 재수립)용 — 시스템 지침·목표·전체 맥락을 다 담는다."""
    return (
        f"{_SYSTEM}\n\n"
        f"## 목표\n{objective}\n\n"
        f"## 사용자 명확화(이미 답한 내용 — 다시 묻지 말고 이에 따라 진행)\n{clar}\n\n"
        f"## 실행한 셸/도구 출력 및 관측 진단\n{tools}\n\n"
        f"## 직전 행동의 postcondition 점검\n{postchecks}\n"
        "위 항목이 있으면 현재 화면에서 먼저 만족/불만족을 판정하고, ★그 판정을 *네가 어차피 낼 다음 행동(action) "
        "객체 안의 post_met 필드*에 true/false 로 같이 적어라. ★★post_met 은 *행동 이름이 아니다* — "
        "action:\"post_met\" 같은 건 존재하지 않는다(허용 action 목록에 없음). 그냥 다음 행동(smart_click/shell/key 등)에 "
        "post_met:true|false 를 함께 실어라. 만족하지 않았으면(false) 같은 행동을 반복하지 말고 버튼 효과/상태 가정을 "
        "수정해 다른 접근을 선택하라. 이 조건이 불만족이면 done 하지 마라.\n\n"
        f"## 지금까지 한 행동\n{hist}\n\n"
        f"## 기기 신호(온디바이스 구조 데이터)\n{device_signals or '(없음)'}\n\n"
        f"## 현재 상태 스냅샷(저비용 구조화 후보)\n{snapshot_text}\n\n"
        f"## 현재 화면\n스크린샷 파일: `{abspath}` — 읽어서 화면을 보고 판단하라.\n\n"
        f"{_ONE_STEP_DOC if one_step else _PLAN_DOC}\n허용 action: {sorted(VALID_ACTIONS)}.\n"
        f"다음 JSON 스키마로만 답하라:\n{_SCHEMA}"
    )


def _delta_prompt(tools, postchecks, snapshot_text, abspath, one_step) -> str:
    """세션 resume 용 — 시스템 지침·목표·스키마는 *세션이 이미 기억*하므로 *새 화면+직전 결과*만 보낸다.
    이게 세션 재사용의 핵심 절감: 매 스텝 거대한 프롬프트를 다시 안 보낸다(지연·비용↓)."""
    return (
        "## 새 화면 (직전 행동 뒤 갱신됨)\n"
        f"스크린샷 파일: `{abspath}` — 읽어서 현재 상태를 보라.\n\n"
        f"## 현재 상태 스냅샷(저비용 구조화 후보)\n{snapshot_text}\n\n"
        f"## 실행한 셸/도구 출력 및 관측 진단(최근)\n{tools}\n\n"
        f"## 직전 행동의 postcondition 점검\n{postchecks}\n"
        "직전 행동의 효과를 이 화면에서 판정해 다음 행동의 post_met(true/false)에 싣고, 목표를 향한 "
        f"{'한 행동만' if one_step else '확신하는 만큼의 행동'}을 *같은 JSON 스키마*로 답하라. "
        "이미 목표를 달성했으면 action:\"done\". (시스템 지침·목표·허용 action·JSON 스키마는 이 세션에서 이미 안다.)\n"
        f"허용 action: {sorted(VALID_ACTIONS)}."
    )


def next_plan(objective: str, history: list[Action], screenshot_path: str,
              model: str = DEFAULT_MODEL, clarifications: list[tuple[str, str]] | None = None,
              tool_outputs: list[tuple[str, str]] | None = None, max_actions: int = 6,
              one_step: bool = False, postcondition_checks: list[str] | None = None,
              state_snapshot: str | None = None,
              device_signals: str | None = None,
              session: dict | None = None) -> list[Action]:
    """현재 화면을 보고 행동을 낸다. 기본='확신하는 만큼 묶음(plan)'(토큰절약).
    one_step=True 면 *한 동작만* — 깨지기 쉬운 앱서 헛클릭 연쇄를 막음(스샷↑·토큰↑ 대가).
    session: {"id": uuid, "started": bool} 주면 claude 세션 재사용(유상태+캐시). 끊기면 새 세션으로 재수립."""
    if one_step:
        max_actions = 1
    hist = "\n".join(f"- {a.short()}" for a in history[-8:]) or "(없음)"
    clar = "\n".join(f"- 질문: {q}\n  사용자 답변: {a}" for q, a in (clarifications or [])) or "(없음)"
    tools = "\n".join(f"- $ {c}\n  → {o}" for c, o in (tool_outputs or [])[-4:]) or "(없음)"
    postchecks = "\n".join(f"- {p}" for p in (postcondition_checks or [])) or "(없음)"
    snap = state_snapshot or "(없음)"
    sigs = device_signals or "(없음)"
    abspath = os.path.abspath(screenshot_path)
    full = _full_prompt(objective, hist, clar, tools, postchecks, snap, sigs, abspath, one_step)
    use_session = bool(session and session.get("id"))
    resume = use_session and bool(session.get("started"))
    sid = session.get("id") if use_session else None
    try:
        if resume:
            data = _cli_call(_delta_prompt(tools, postchecks, snap, abspath, one_step), abspath, model,
                             session_id=sid, resume=True)
        else:
            data = _cli_call(full, abspath, model, session_id=sid, resume=False)
        if use_session:
            session["started"] = True
    except subprocess.TimeoutExpired:
        return [Action(action="abort", thought="계획 호출이 반복 타임아웃(인프라 간헐 행) — 안전 중단")]
    except Exception:
        # ★세션 끊김(만료/손상) → 새 세션 id 로 *풀 프롬프트* 재수립. 다음 스텝부터 다시 resume.
        if use_session:
            import uuid as _uuid
            session["id"] = str(_uuid.uuid4())
            session["started"] = False
            try:
                data = _cli_call(full, abspath, model, session_id=session["id"], resume=False)
                session["started"] = True
            except Exception:
                return [Action(action="abort", thought="세션 재수립 실패 — 안전 중단")]
        else:
            return [Action(action="abort", thought="brain 호출 실패 — 안전 중단")]
    raw = data.get("actions")
    if isinstance(raw, list) and raw:
        actions = [Action.from_dict(a) for a in raw][:max_actions]
        if actions and not actions[0].thought:   # plan 전체의 생각을 첫 행동에 실어 보이게
            actions[0].thought = data.get("thought", "")
        return actions
    if data.get("action"):                       # 단일 행동 형태 폴백
        return [Action.from_dict(data)]
    return [Action(action="abort", thought="빈 plan(파싱 실패)")]


def next_action(objective: str, history: list[Action], screenshot_path: str,
                model: str = DEFAULT_MODEL, clarifications: list[tuple[str, str]] | None = None,
                tool_outputs: list[tuple[str, str]] | None = None) -> Action:
    """단일 행동(RUBI 검증 등에서 첫 결정만 볼 때). plan의 첫 행동."""
    return next_plan(objective, history, screenshot_path, model, clarifications, tool_outputs, max_actions=1)[0]


def _cli_call(prompt: str, abspath: str, model: str,
              timeout: int = 90, retries: int = 2,
              session_id: str | None = None, resume: bool = False) -> dict:
    """LLM 호출(provider 추상화 — claude CLI 기본 / SAPPHI_PROVIDER=openai 면 GPT).
    ★claude 는 정상 ~18s인데 간헐적으로 행이 걸려 — provider 가 타임아웃 시 재시도한다.
    session_id/resume: 세션 재사용(유상태+캐시). resume=True 면 시스템 프롬프트를 다시 안 보내 더 빠르다."""
    from rubi.provider import complete_json
    return complete_json(prompt, model, image_path=abspath, timeout=timeout, retries=retries,
                         session_id=session_id, resume=resume)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # ★중요: 파싱 실패를 'done'으로 처리하면 무음 허위 성공이 된다(RUBI #1).
        # 정직하게 'abort'(안전 중단)로 처리해 실패가 실패로 드러나게 한다.
        return {"action": "abort", "thought": "응답 파싱 실패 — 안전하게 중단"}
