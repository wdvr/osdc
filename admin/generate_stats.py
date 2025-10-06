#!/usr/bin/env python3
"""
GPU Dev Server Usage Analytics
Generates statistics and visualizations from DynamoDB reservation data
"""

import boto3
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from datetime import datetime, timedelta
from collections import defaultdict
import json
import os

# Set style
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (12, 6)
plt.rcParams['font.size'] = 10

# AWS Configuration
REGION = os.environ.get('AWS_REGION', 'us-east-2')
TABLE_NAME = os.environ.get('RESERVATIONS_TABLE', 'pytorch-gpu-dev-reservations')

# Output directory
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def fetch_all_reservations():
    """Fetch all reservations from DynamoDB"""
    print("Fetching reservations from DynamoDB...")
    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)

    reservations = []
    last_evaluated_key = None

    while True:
        if last_evaluated_key:
            response = table.scan(ExclusiveStartKey=last_evaluated_key)
        else:
            response = table.scan()

        reservations.extend(response.get('Items', []))

        last_evaluated_key = response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break

    print(f"Fetched {len(reservations)} reservations")
    return reservations


def parse_reservation_data(reservations):
    """Parse reservation data into a DataFrame"""
    print("Parsing reservation data...")

    data = []
    for res in reservations:
        try:
            # Parse created_at (can be ISO string or timestamp)
            created_at_raw = res.get('created_at', '')
            if isinstance(created_at_raw, str):
                # ISO 8601 format: "2025-10-03T03:09:06.002555"
                created_at = datetime.fromisoformat(created_at_raw.replace('Z', '+00:00'))
            else:
                # Numeric timestamp
                created_at = datetime.fromtimestamp(float(created_at_raw))

            # Parse expires_at (can be ISO string or timestamp)
            expires_at_raw = res.get('expires_at', '')
            expires_at = None
            if expires_at_raw:
                if isinstance(expires_at_raw, str):
                    expires_at = datetime.fromisoformat(expires_at_raw.replace('Z', '+00:00'))
                else:
                    expires_at = datetime.fromtimestamp(float(expires_at_raw))

            # Calculate duration
            duration_hours = 0
            if expires_at and expires_at > created_at:
                duration_hours = (expires_at - created_at).total_seconds() / 3600

            data.append({
                'reservation_id': res.get('reservation_id', ''),
                'user_id': res.get('user_id', ''),
                'gpu_type': res.get('gpu_type', '').lower(),  # Normalize to lowercase
                'gpu_count': int(res.get('gpu_count', 1)),
                'status': res.get('status', ''),
                'created_at': created_at,
                'expires_at': expires_at,
                'duration_hours': duration_hours,
            })
        except Exception as e:
            print(f"Warning: Failed to parse reservation: {e}")
            continue

    df = pd.DataFrame(data)
    print(f"Parsed {len(df)} valid reservations")
    return df


def calculate_statistics(df):
    """Calculate key statistics"""
    print("\nCalculating statistics...")

    stats = {
        'total_reservations': len(df),
        'unique_users': df['user_id'].nunique(),
        'date_range': {
            'first': df['created_at'].min(),
            'last': df['created_at'].max(),
        },
        'gpu_types': df['gpu_type'].value_counts().to_dict(),
        'status_breakdown': df['status'].value_counts().to_dict(),
        'total_gpu_hours': (df['duration_hours'] * df['gpu_count']).sum(),
    }

    return stats


def plot_daily_active_reservations(df):
    """Plot daily active reservation counts for last 4 weeks"""
    print("\nGenerating daily active reservations plot...")

    # Get last 4 weeks
    end_date = datetime.now()
    start_date = end_date - timedelta(weeks=4)

    # Filter to last 4 weeks
    df_recent = df[df['created_at'] >= start_date].copy()

    # Create date range for last 4 weeks
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')

    # Count active reservations per day
    daily_active = []
    for date in date_range:
        active = df_recent[
            (df_recent['created_at'] <= date) &
            ((df_recent['expires_at'].isna()) | (df_recent['expires_at'] >= date))
        ]
        daily_active.append(len(active))

    # Plot
    plt.figure(figsize=(14, 6))
    plt.plot(date_range, daily_active, marker='o', linewidth=2, markersize=4)
    plt.title('Daily Active Reservations (Last 4 Weeks)', fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Number of Active Reservations', fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'daily_active_reservations.png'), dpi=300, bbox_inches='tight')
    print(f"  Saved: {OUTPUT_DIR}/daily_active_reservations.png")
    plt.close()


def plot_hourly_gpu_usage(df):
    """Plot hourly active GPU count for last 4 weeks"""
    print("\nGenerating hourly GPU usage plot...")

    # Get last 4 weeks
    end_date = datetime.now()
    start_date = end_date - timedelta(weeks=4)

    # Filter to last 4 weeks
    df_recent = df[df['created_at'] >= start_date].copy()

    # Create hourly range for last 4 weeks
    hour_range = pd.date_range(start=start_date, end=end_date, freq='H')

    # Count active GPUs per hour
    hourly_gpus = []
    for hour in hour_range:
        active = df_recent[
            (df_recent['created_at'] <= hour) &
            ((df_recent['expires_at'].isna()) | (df_recent['expires_at'] >= hour))
        ]
        total_gpus = (active['gpu_count']).sum()
        hourly_gpus.append(total_gpus)

    # Plot
    plt.figure(figsize=(16, 6))
    plt.plot(hour_range, hourly_gpus, linewidth=1, alpha=0.8)
    plt.fill_between(hour_range, hourly_gpus, alpha=0.3)
    plt.title('Hourly Active GPU Count (Last 4 Weeks)', fontsize=16, fontweight='bold')
    plt.xlabel('Date', fontsize=12)
    plt.ylabel('Number of Active GPUs', fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=3))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'hourly_gpu_usage.png'), dpi=300, bbox_inches='tight')
    print(f"  Saved: {OUTPUT_DIR}/hourly_gpu_usage.png")
    plt.close()


def plot_gpu_type_distribution(df):
    """Plot GPU type distribution"""
    print("\nGenerating GPU type distribution plot...")

    gpu_counts = df['gpu_type'].value_counts()

    plt.figure(figsize=(10, 6))
    colors = sns.color_palette("husl", len(gpu_counts))
    plt.bar(range(len(gpu_counts)), gpu_counts.values, color=colors)
    plt.xticks(range(len(gpu_counts)), gpu_counts.index, rotation=45, ha='right')
    plt.title('Reservations by GPU Type', fontsize=16, fontweight='bold')
    plt.xlabel('GPU Type', fontsize=12)
    plt.ylabel('Number of Reservations', fontsize=12)
    plt.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'gpu_type_distribution.png'), dpi=300, bbox_inches='tight')
    print(f"  Saved: {OUTPUT_DIR}/gpu_type_distribution.png")
    plt.close()


def plot_top_users(df, top_n=10):
    """Plot top users by reservation count"""
    print("\nGenerating top users plot...")

    user_counts = df['user_id'].value_counts().head(top_n)

    plt.figure(figsize=(12, 6))
    colors = sns.color_palette("viridis", len(user_counts))
    plt.barh(range(len(user_counts)), user_counts.values, color=colors)
    plt.yticks(range(len(user_counts)), [u.split('@')[0] for u in user_counts.index])
    plt.title(f'Top {top_n} Users by Reservation Count', fontsize=16, fontweight='bold')
    plt.xlabel('Number of Reservations', fontsize=12)
    plt.ylabel('User', fontsize=12)
    plt.grid(True, alpha=0.3, axis='x')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'top_users.png'), dpi=300, bbox_inches='tight')
    print(f"  Saved: {OUTPUT_DIR}/top_users.png")
    plt.close()


def plot_top_users_by_gpu_hours(df, top_n=10):
    """Plot top users by GPU hours, grouped by GPU type (stacked bar)"""
    print("\nGenerating top users by GPU hours plot...")

    # Calculate GPU hours per user per GPU type
    df['gpu_hours'] = df['duration_hours'] * df['gpu_count']

    # Get top N users by total GPU hours
    top_users = df.groupby('user_id')['gpu_hours'].sum().nlargest(top_n).index

    # Filter to top users and pivot for stacking
    df_top = df[df['user_id'].isin(top_users)].copy()
    user_gpu_type_hours = df_top.groupby(['user_id', 'gpu_type'])['gpu_hours'].sum().unstack(fill_value=0)

    # Sort by total GPU hours
    user_gpu_type_hours['total'] = user_gpu_type_hours.sum(axis=1)
    user_gpu_type_hours = user_gpu_type_hours.sort_values('total', ascending=True)
    user_gpu_type_hours = user_gpu_type_hours.drop('total', axis=1)

    # Plot stacked horizontal bar chart
    plt.figure(figsize=(12, 8))
    colors = sns.color_palette("Set2", len(user_gpu_type_hours.columns))

    user_gpu_type_hours.plot(
        kind='barh',
        stacked=True,
        color=colors,
        figsize=(12, 8)
    )

    # Format y-axis labels (remove @domain.com)
    labels = [u.split('@')[0] for u in user_gpu_type_hours.index]
    plt.yticks(range(len(labels)), labels)

    plt.title(f'Top {top_n} Users by GPU Hours (by GPU Type)', fontsize=16, fontweight='bold')
    plt.xlabel('GPU Hours', fontsize=12)
    plt.ylabel('User', fontsize=12)
    plt.legend(title='GPU Type', bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.grid(True, alpha=0.3, axis='x')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, 'top_users_gpu_hours.png'), dpi=300, bbox_inches='tight')
    print(f"  Saved: {OUTPUT_DIR}/top_users_gpu_hours.png")
    plt.close()


def generate_html_dashboard(stats, df):
    """Generate HTML dashboard"""
    print("\nGenerating HTML dashboard...")

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPU Dev Server Analytics Dashboard</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        h1 {{
            color: white;
            text-align: center;
            margin-bottom: 10px;
            font-size: 2.5em;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}

        .subtitle {{
            color: rgba(255,255,255,0.9);
            text-align: center;
            margin-bottom: 30px;
            font-size: 1.1em;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.2s;
        }}

        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }}

        .stat-value {{
            font-size: 2.5em;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 5px;
        }}

        .stat-label {{
            color: #666;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .charts {{
            display: grid;
            gap: 20px;
        }}

        .chart-card {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}

        .chart-card img {{
            width: 100%;
            height: auto;
            border-radius: 8px;
        }}

        .chart-title {{
            font-size: 1.3em;
            color: #333;
            margin-bottom: 15px;
            font-weight: 600;
        }}

        .gpu-types {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}

        .gpu-type-item {{
            display: flex;
            justify-content: space-between;
            padding: 10px;
            border-bottom: 1px solid #eee;
        }}

        .gpu-type-item:last-child {{
            border-bottom: none;
        }}

        .footer {{
            text-align: center;
            color: rgba(255,255,255,0.8);
            margin-top: 40px;
            padding: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 GPU Dev Server Analytics</h1>
        <p class="subtitle">Generated on {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</p>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{stats['total_reservations']:,}</div>
                <div class="stat-label">Total Reservations</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats['unique_users']:,}</div>
                <div class="stat-label">Unique Users</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats['total_gpu_hours']:,.0f}</div>
                <div class="stat-label">Total GPU Hours</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(df[df['status'] == 'active']):,}</div>
                <div class="stat-label">Currently Active</div>
            </div>
        </div>

        <div class="gpu-types">
            <h2 class="chart-title">GPU Type Distribution</h2>
            {''.join([f'<div class="gpu-type-item"><span><strong>{gpu_type}</strong></span><span>{count} reservations</span></div>' for gpu_type, count in stats['gpu_types'].items()])}
        </div>

        <div class="charts">
            <div class="chart-card">
                <h2 class="chart-title">Daily Active Reservations (Last 4 Weeks)</h2>
                <img src="daily_active_reservations.png" alt="Daily Active Reservations">
            </div>

            <div class="chart-card">
                <h2 class="chart-title">Hourly Active GPU Count (Last 4 Weeks)</h2>
                <img src="hourly_gpu_usage.png" alt="Hourly GPU Usage">
            </div>

            <div class="chart-card">
                <h2 class="chart-title">Reservations by GPU Type</h2>
                <img src="gpu_type_distribution.png" alt="GPU Type Distribution">
            </div>

            <div class="chart-card">
                <h2 class="chart-title">Top 10 Users by Reservation Count</h2>
                <img src="top_users.png" alt="Top Users">
            </div>

            <div class="chart-card">
                <h2 class="chart-title">Top 10 Users by GPU Hours (by Type)</h2>
                <img src="top_users_gpu_hours.png" alt="Top Users by GPU Hours">
            </div>
        </div>

        <div class="footer">
            <p>Data spans from {stats['date_range']['first'].strftime('%B %d, %Y')} to {stats['date_range']['last'].strftime('%B %d, %Y')}</p>
        </div>
    </div>
</body>
</html>
    """

    output_path = os.path.join(OUTPUT_DIR, 'dashboard.html')
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"  Saved: {output_path}")


def main():
    """Main execution"""
    print("=" * 60)
    print("GPU Dev Server Usage Analytics")
    print("=" * 60)

    # Fetch data
    reservations = fetch_all_reservations()
    df = parse_reservation_data(reservations)

    if df.empty:
        print("No reservation data found!")
        return

    # Calculate statistics
    stats = calculate_statistics(df)

    print("\n" + "=" * 60)
    print("KEY STATISTICS")
    print("=" * 60)
    print(f"Total Reservations: {stats['total_reservations']:,}")
    print(f"Unique Users: {stats['unique_users']:,}")
    print(f"Total GPU Hours: {stats['total_gpu_hours']:,.0f}")
    print(f"Date Range: {stats['date_range']['first'].strftime('%Y-%m-%d')} to {stats['date_range']['last'].strftime('%Y-%m-%d')}")
    print(f"\nGPU Types:")
    for gpu_type, count in stats['gpu_types'].items():
        print(f"  {gpu_type}: {count}")
    print(f"\nStatus Breakdown:")
    for status, count in stats['status_breakdown'].items():
        print(f"  {status}: {count}")

    # Generate plots
    print("\n" + "=" * 60)
    print("GENERATING VISUALIZATIONS")
    print("=" * 60)
    plot_daily_active_reservations(df)
    plot_hourly_gpu_usage(df)
    plot_gpu_type_distribution(df)
    plot_top_users(df)
    plot_top_users_by_gpu_hours(df)

    # Generate dashboard
    generate_html_dashboard(stats, df)

    print("\n" + "=" * 60)
    print("✅ Complete! Open dashboard.html in your browser")
    print(f"   Location: {os.path.join(OUTPUT_DIR, 'dashboard.html')}")
    print("=" * 60)


if __name__ == '__main__':
    main()
