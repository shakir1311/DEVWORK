#!/usr/bin/env python3
"""
Generate figures for thesis from actual experimental data.
All data comes directly from our experiments - no fake values.
"""

import json
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import numpy as np
from pathlib import Path
from collections import defaultdict

# Paths
EDGE_DIR = Path(__file__).parent
RESULTS_FILE = EDGE_DIR / 'experiment_results/ledger_on_xai_off_20251220_203805/results.jsonl'
LEDGER_FILE = EDGE_DIR / 'experiment_results/ledger_timing_controlled/controlled_comparison.json'
OUTPUT_DIR = Path('/Volumes/Stuff/GDrive2026/Abertay/research/DEVWORK/figures')

# Create output directory
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load classification results
print("Loading results...")
with open(RESULTS_FILE, 'r') as f:
    results = [json.loads(line) for line in f if line.strip()]

# Load ledger timing data
with open(LEDGER_FILE, 'r') as f:
    ledger_data = json.load(f)

print(f"Loaded {len(results)} classification results")

# ============================================================================
# FIGURE 1: Confusion Matrix
# ============================================================================
print("\nGenerating Figure 1: Confusion Matrix...")

classes = ['N', 'A', 'O', '~']
class_labels = ['Normal', 'AFib', 'Other', 'Noisy']
confusion = np.zeros((4, 4), dtype=int)
class_to_idx = {c: i for i, c in enumerate(classes)}

for r in results:
    if r['success']:
        gt_idx = class_to_idx[r['ground_truth']]
        pred_idx = class_to_idx[r['predicted_class']]
        confusion[gt_idx][pred_idx] += 1

fig, ax = plt.subplots(figsize=(8, 6))
im = ax.imshow(confusion, cmap='Blues')

# Add colorbar
cbar = ax.figure.colorbar(im, ax=ax)
cbar.ax.set_ylabel('Count', rotation=-90, va="bottom")

# Add labels
ax.set_xticks(np.arange(4))
ax.set_yticks(np.arange(4))
ax.set_xticklabels(class_labels)
ax.set_yticklabels(class_labels)
ax.set_xlabel('Predicted Class')
ax.set_ylabel('Actual Class')
ax.set_title('Confusion Matrix (n=8,528)')

# Add text annotations
for i in range(4):
    for j in range(4):
        color = 'white' if confusion[i, j] > confusion.max() / 2 else 'black'
        ax.text(j, i, f'{confusion[i, j]:,}', ha='center', va='center', color=color, fontsize=12)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / 'confusion_matrix.pdf', dpi=300, bbox_inches='tight')
plt.savefig(OUTPUT_DIR / 'confusion_matrix.png', dpi=300, bbox_inches='tight')
plt.close()

print(f"  Saved: confusion_matrix.pdf")
print(f"  Matrix values: {confusion.tolist()}")

# ============================================================================
# FIGURE 2: Per-Class Metrics
# ============================================================================
print("\nGenerating Figure 2: Per-Class Metrics...")

# Calculate metrics from confusion matrix
metrics = {}
for i, c in enumerate(classes):
    tp = confusion[i, i]
    fp = confusion[:, i].sum() - tp
    fn = confusion[i, :].sum() - tp
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    metrics[c] = {'precision': precision, 'recall': recall, 'f1': f1}
    print(f"  {c}: P={precision:.3f}, R={recall:.3f}, F1={f1:.3f}")

fig, ax = plt.subplots(figsize=(10, 6))

x = np.arange(4)
width = 0.25

precision_vals = [metrics[c]['precision'] for c in classes]
recall_vals = [metrics[c]['recall'] for c in classes]
f1_vals = [metrics[c]['f1'] for c in classes]

bars1 = ax.bar(x - width, precision_vals, width, label='Precision', color='#2196F3')
bars2 = ax.bar(x, recall_vals, width, label='Recall', color='#4CAF50')
bars3 = ax.bar(x + width, f1_vals, width, label='F1-Score', color='#FF9800')

ax.set_xlabel('Class')
ax.set_ylabel('Score')
ax.set_title('Per-Class Classification Metrics')
ax.set_xticks(x)
ax.set_xticklabels(class_labels)
ax.set_ylim(0, 1.0)
ax.legend()
ax.grid(axis='y', alpha=0.3)

# Add value labels on bars
for bars in [bars1, bars2, bars3]:
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f'{height:.2f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points",
                    ha='center', va='bottom', fontsize=8)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / 'per_class_metrics.pdf', dpi=300, bbox_inches='tight')
plt.savefig(OUTPUT_DIR / 'per_class_metrics.png', dpi=300, bbox_inches='tight')
plt.close()

print(f"  Saved: per_class_metrics.pdf")

# ============================================================================
# FIGURE 3: Error Distribution
# ============================================================================
print("\nGenerating Figure 3: Error Distribution...")

# Count errors by type
errors = defaultdict(int)
for r in results:
    if r['success'] and r['predicted_class'] != r['ground_truth']:
        key = f"{r['ground_truth']}→{r['predicted_class']}"
        errors[key] += 1

# Sort by count and take top 6
sorted_errors = sorted(errors.items(), key=lambda x: -x[1])[:6]
error_labels = [e[0] for e in sorted_errors]
error_counts = [e[1] for e in sorted_errors]
total_errors = sum(errors.values())

fig, ax = plt.subplots(figsize=(10, 6))

colors = ['#E53935', '#FB8C00', '#FDD835', '#43A047', '#1E88E5', '#8E24AA']
bars = ax.barh(error_labels, error_counts, color=colors)

ax.set_xlabel('Number of Errors')
ax.set_ylabel('Error Type (Actual→Predicted)')
ax.set_title(f'Top 6 Misclassification Types (Total Errors: {total_errors})')
ax.invert_yaxis()

# Add count labels
for bar, count in zip(bars, error_counts):
    width = bar.get_width()
    pct = count / total_errors * 100
    ax.text(width + 5, bar.get_y() + bar.get_height()/2, 
            f'{count} ({pct:.1f}%)', va='center', fontsize=10)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / 'error_distribution.pdf', dpi=300, bbox_inches='tight')
plt.savefig(OUTPUT_DIR / 'error_distribution.png', dpi=300, bbox_inches='tight')
plt.close()

print(f"  Saved: error_distribution.pdf")
for e, c in sorted_errors:
    print(f"    {e}: {c}")

# ============================================================================
# FIGURE 4: Ledger Performance Comparison
# ============================================================================
print("\nGenerating Figure 4: Ledger Performance Comparison...")

ledger_off = ledger_data['ledger_off']
ledger_on = ledger_data['ledger_on']

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

# Left: Insert Time Comparison
metrics_names = ['Median', 'Average']
off_vals = [ledger_off['median_insert_time_ms'], ledger_off['avg_insert_time_ms']]
on_vals = [ledger_on['median_insert_time_ms'], ledger_on['avg_insert_time_ms']]

x = np.arange(2)
width = 0.35

bars1 = ax1.bar(x - width/2, off_vals, width, label='Ledger OFF', color='#4CAF50')
bars2 = ax1.bar(x + width/2, on_vals, width, label='Ledger ON', color='#2196F3')

ax1.set_xlabel('Metric')
ax1.set_ylabel('Insert Time (ms)')
ax1.set_title('Database Insert Time: Ledger ON vs OFF')
ax1.set_xticks(x)
ax1.set_xticklabels(metrics_names)
ax1.legend()
ax1.grid(axis='y', alpha=0.3)

# Add value labels
for bars in [bars1, bars2]:
    for bar in bars:
        height = bar.get_height()
        ax1.annotate(f'{height:.2f}',
                     xy=(bar.get_x() + bar.get_width() / 2, height),
                     xytext=(0, 3), textcoords="offset points",
                     ha='center', va='bottom', fontsize=10)

# Right: Throughput Comparison
throughputs = [ledger_off['records_per_second'], ledger_on['records_per_second']]
labels = ['Ledger OFF', 'Ledger ON']
colors = ['#4CAF50', '#2196F3']

bars = ax2.bar(labels, throughputs, color=colors)
ax2.set_ylabel('Records per Second')
ax2.set_title('Throughput: Ledger ON vs OFF')
ax2.grid(axis='y', alpha=0.3)

for bar in bars:
    height = bar.get_height()
    ax2.annotate(f'{height:.0f}',
                 xy=(bar.get_x() + bar.get_width() / 2, height),
                 xytext=(0, 3), textcoords="offset points",
                 ha='center', va='bottom', fontsize=12)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / 'ledger_performance.pdf', dpi=300, bbox_inches='tight')
plt.savefig(OUTPUT_DIR / 'ledger_performance.png', dpi=300, bbox_inches='tight')
plt.close()

print(f"  Saved: ledger_performance.pdf")
print(f"  Ledger OFF: median={off_vals[0]:.2f}ms, {throughputs[0]:.0f} rec/s")
print(f"  Ledger ON:  median={on_vals[0]:.2f}ms, {throughputs[1]:.0f} rec/s")
print(f"  Overhead: {ledger_data['overhead_ms']:.2f}ms ({ledger_data['overhead_percent']:.1f}%)")

print("\n" + "="*60)
print("ALL FIGURES GENERATED SUCCESSFULLY")
print("="*60)
print(f"Output directory: {OUTPUT_DIR}")
