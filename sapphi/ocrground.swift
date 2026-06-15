// ocrground — macOS 내장 Vision OCR로 화면 텍스트+좌표를 뽑는 작은 CLI.
// RUBI 사전비평 반영:
//  ★좌표변환을 여기(Swift)서 완결한다 — Vision boundingBox는 *정규화(0~1)·좌하단 원점*.
//    클릭(pyautogui)은 *논리포인트·좌상단 원점*. 그래서:
//      cx_pt = box.midX            * logicalW           (정규화→논리포인트, 곱만)
//      cy_pt = (1 - box.midY)      * logicalH           (★Y축 뒤집기)
//    정규화값이라 이미지 픽셀해상도/Retina scale 자체가 무관 — 논리치수만 곱하면 끝.
//    (logicalW/H 는 Python이 pyautogui.size()로 넘겨줘 클릭 좌표공간과 100% 일치시킨다.)
//  ★한국어 인식 명시. usesLanguageCorrection=false (UI 라벨 오교정 방지).
//  ★권한/로드 실패는 종료코드 비0 + stderr 로 명확히 — 무음 폴백 금지.
//
// 빌드: swiftc -O ocrground.swift -o ocrground
// 사용: ./ocrground <image.png> <logicalW> <logicalH>   →  stdout: JSON 배열

import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 4,
      let logicalW = Double(args[2]),
      let logicalH = Double(args[3]) else {
    FileHandle.standardError.write("usage: ocrground <image> <logicalW> <logicalH>\n".data(using: .utf8)!)
    exit(2)
}
let path = args[1]

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("ERR_LOAD: 이미지 로드 실패: \(path)\n".data(using: .utf8)!)
    exit(3)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate            // 작은 한글 UI 텍스트 정확도 우선
request.usesLanguageCorrection = false          // RUBI: 한/영 혼용 UI 오교정 방지
request.recognitionLanguages = ["ko-KR", "en-US"]

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("ERR_OCR: \(error)\n".data(using: .utf8)!)
    exit(4)
}

let results = (request.results as? [VNRecognizedTextObservation]) ?? []
var items: [[String: Any]] = []
for obs in results {
    guard let top = obs.topCandidates(1).first else { continue }
    let bb = obs.boundingBox                     // 정규화, 좌하단 원점
    let cx = bb.midX * logicalW
    let cy = (1.0 - bb.midY) * logicalH          // ★Y 뒤집기 → 좌상단 원점 논리포인트
    items.append([
        "text": top.string,
        "cx": cx, "cy": cy,
        "w": bb.width * logicalW, "h": bb.height * logicalH,
        "conf": top.confidence,
    ])
}

do {
    let data = try JSONSerialization.data(withJSONObject: items, options: [])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write("ERR_JSON: \(error)\n".data(using: .utf8)!)
    exit(5)
}
