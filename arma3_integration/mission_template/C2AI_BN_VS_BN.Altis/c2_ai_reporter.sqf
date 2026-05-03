/*
 * C2 AI Reporter - ARMA3 SQF 데이터 수집 스크립트
 *
 * [설치 방법]
 * 미션 폴더에 이 파일을 복사하고 description.ext 또는 init.sqf에서 호출:
 *   execVM "c2_ai_reporter.sqf";
 *
 * [동작 방식]
 * - 주기적으로 전장 상태를 수집하여 diag_log에 [C2AI_DATA] 접두사로 기록
 * - 로컬 relay.py가 ARMA3 .rpt 로그를 감시하다가 Colab 서버로 전송
 */

// ── 설정 ─────────────────────────────────────────────────────────
C2AI_REPORT_INTERVAL = 10;    // 데이터 전송 주기 (초)
C2AI_MAX_UNITS       = 200;   // 최대 수집 유닛 수 (성능)

// ── JSON 빌더 유틸 ───────────────────────────────────────────────

// 문자열 내 특수문자 이스케이프
C2AI_fnc_escapeStr = {
    params ["_s"];
    _s = [_s, """", "\\"""] call BIS_fnc_replaceString;
    _s
};

// side → 문자열
C2AI_fnc_sideStr = {
    params ["_side"];
    if (_side == EAST)        exitWith { "OPFOR" };
    if (_side == WEST)        exitWith { "BLUFOR" };
    if (_side == INDEPENDENT) exitWith { "INDEP" };
    if (_side == CIVILIAN)    exitWith { "CIV" };
    "UNKNOWN"
};

// 유닛 카테고리 분류
C2AI_fnc_category = {
    params ["_u"];
    if (_u isKindOf "Tank")       exitWith { "armor" };
    if (_u isKindOf "APC")        exitWith { "apc" };
    if (_u isKindOf "Helicopter") exitWith { "helicopter" };
    if (_u isKindOf "Plane")      exitWith { "aircraft" };
    if (_u isKindOf "Ship")       exitWith { "naval" };
    if (_u isKindOf "Car")        exitWith { "vehicle" };
    if (_u isKindOf "Truck")      exitWith { "truck" };
    if (_u isKindOf "Man")        exitWith { "infantry" };
    "unknown"
};

// 유닛 → JSON 객체 문자열
C2AI_fnc_unitToJson = {
    params ["_u"];
    private _pos    = getPosASL _u;
    private _px     = round ((_pos select 0) * 10) / 10;
    private _py     = round ((_pos select 1) * 10) / 10;
    private _hp     = round ((1 - damage _u) * 100);
    private _type   = [typeOf _u]        call C2AI_fnc_escapeStr;
    private _side   = [side _u]          call C2AI_fnc_sideStr;
    private _cat    = [_u]               call C2AI_fnc_category;
    private _grpId  = [groupId group _u] call C2AI_fnc_escapeStr;
    private _id     = netId _u;
    format [
        "{""id"":""%1"",""type"":""%2"",""side"":""%3"",""cat"":""%4"",""hp"":%5,""x"":%6,""y"":%7,""grp"":""%8""}",
        _id, _type, _side, _cat, _hp, _px, _py, _grpId
    ]
};

// 그룹 → JSON 객체 문자열
C2AI_fnc_groupToJson = {
    params ["_g"];
    private _lead   = leader _g;
    private _pos    = getPosASL _lead;
    private _px     = round ((_pos select 0) * 10) / 10;
    private _py     = round ((_pos select 1) * 10) / 10;
    private _side   = [side _g] call C2AI_fnc_sideStr;
    private _gid    = [groupId _g] call C2AI_fnc_escapeStr;
    private _alive  = { alive _x } count (units _g);
    format [
        "{""id"":""%1"",""side"":""%2"",""strength"":%3,""x"":%4,""y"":%5}",
        _gid, _side, _alive, _px, _py
    ]
};

// ── 메인 수집 루프 ───────────────────────────────────────────────
diag_log "[C2AI_DATA] Reporter started";

while { true } do {
    // 유닛 수집 (생존 유닛만)
    private _allUnits = (entities "All") select {
        alive _x && !(_x isKindOf "Logic") && !(_x isKindOf "WeaponHolder")
    };

    // 수량 제한
    if (count _allUnits > C2AI_MAX_UNITS) then {
        _allUnits = _allUnits select [0, C2AI_MAX_UNITS];
    };

    private _unitJsonArr = _allUnits apply { [_x] call C2AI_fnc_unitToJson };
    private _unitStr     = "[" + (_unitJsonArr joinString ",") + "]";

    // 그룹 수집
    private _allGroups = allGroups select {
        private _alive = { alive _x } count (units _x);
        _alive > 0
    };
    private _grpJsonArr = _allGroups apply { [_x] call C2AI_fnc_groupToJson };
    private _grpStr     = "[" + (_grpJsonArr joinString ",") + "]";

    // 병력 요약 (진영별 카테고리별 수량)
    private _opforInf    = { alive _x && side _x == EAST && _x isKindOf "Man" }    count (entities "All");
    private _opforArmor  = { alive _x && side _x == EAST && _x isKindOf "Tank" }   count (entities "All");
    private _opforHelo   = { alive _x && side _x == EAST && _x isKindOf "Helicopter" } count (entities "All");
    private _bluforInf   = { alive _x && side _x == WEST && _x isKindOf "Man" }    count (entities "All");
    private _bluforArmor = { alive _x && side _x == WEST && _x isKindOf "Tank" }   count (entities "All");
    private _bluforHelo  = { alive _x && side _x == WEST && _x isKindOf "Helicopter" } count (entities "All");

    private _summary = format [
        "{""opfor"":{""infantry"":%1,""armor"":%2,""helicopter"":%3},""blufor"":{""infantry"":%4,""armor"":%5,""helicopter"":%6}}",
        _opforInf, _opforArmor, _opforHelo,
        _bluforInf, _bluforArmor, _bluforHelo
    ];

    // 최종 페이로드 출력
    private _payload = format [
        "[C2AI_DATA]{""t"":%1,""units"":%2,""groups"":%3,""summary"":%4}",
        round time,
        _unitStr,
        _grpStr,
        _summary
    ];
    diag_log _payload;

    sleep C2AI_REPORT_INTERVAL;
};
