"""Sapphi — 컴퓨터 제어 AI 비서 (유행하는 self-operating-computer 패턴 구현).

표준 루프: 스크린샷 → Claude(화면 보고 다음 행동 결정) → pyautogui 실행 → 목표까지.
우리 차별점: 그 repo들에 없는 **리허설+게이팅 안전층**을 코어에 내장.
  - plan      : 실제 제어 없이 계획만 (부작용 0)
  - rehearse  : 가역 행동만 실행, 비가역 '커밋' 직전에서 정지 (기본 안전값)
  - live      : 전부 실행하되 비가역 행동은 사람 확인(y/n) 게이팅

인증은 RUBI와 동일하게 '로그인된 claude CLI'(키 불필요)를 재사용한다.
이 Sapphi 가 곧 RUBI 의 테스트 대상(AUT)이 된다.
"""

__version__ = "0.1.0"
