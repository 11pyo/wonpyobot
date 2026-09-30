# -*- coding: utf-8 -*-
"""
trust_bound.py — 「연속 무오류 N건」이 정확도에 대해 실제로 말해 주는 것

Wilson 95% 신뢰구간 하한. 표본이 작으면 전부 맞아도 「정확도가 얼마 이상」이라고 말할 수
있는 폭이 좁다. 자동화 단계의 기준 건수(예: 20건 연속 무오류)는 안전의 증명이 아니라
**속도 관문**이고, 그 옆에 이 하한을 함께 표시해야 한다.

    python trust_bound.py                 → 표(1 · 20 · 50 · 100건 무오류)와 필요 건수
    python trust_bound.py 35 73           → 그 건수만큼 무오류일 때의 하한
    python trust_bound.py --need 0.9      → 정확도가 최소 90% 라고 말하려면 필요한 무오류 건수
    python trust_bound.py 50 --wrong 2    → 50건 중 2건 틀렸을 때의 하한

English: lower bound (Wilson, 95%) of accuracy after n answers with k wrong. A streak of 20
correct answers only supports "at least ~84% accurate" — a speed gate, not a safety proof.
"""

import sys

Z = 1.959964  # 95% 양측


def wilson_low(correct, n, z=Z):
    """n건 중 correct건이 맞았을 때 정확도의 95% 신뢰구간 하한(0~1)."""
    if n <= 0:
        return 0.0
    p = correct / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (c - m) / d)


def zero_error_need(min_acc, z=Z):
    """무오류 연속 n건의 하한이 min_acc(0<min_acc<1) 이상이 되는 가장 작은 n."""
    if not 0 < min_acc < 1:
        raise ValueError("min_acc 는 0 과 1 사이여야 한다")
    n = 1
    while wilson_low(n, n, z) < min_acc:
        n += 1
    return n


def main(argv):
    wrong = 0
    if "--wrong" in argv:
        i = argv.index("--wrong")
        wrong = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--need" in argv:
        i = argv.index("--need")
        acc = float(argv[i + 1])
        print("정확도가 최소 %d%% 라고 말하려면 무오류 %d건이 필요하다(95%% 신뢰)." % (round(acc * 100), zero_error_need(acc)))
        return 0
    ns = [int(a) for a in argv] or [1, 20, 50, 100]
    print("| 표본 | 틀림 | 정확도 하한(95%) |")
    print("|---|---|---|")
    for n in ns:
        print("| %d건 | %d | %.0f%% |" % (n, wrong, wilson_low(n - wrong, n) * 100))
    if not argv:
        print("\n정확도 최소 90%%: 무오류 %d건 · 95%%: %d건" % (zero_error_need(0.90), zero_error_need(0.95)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
