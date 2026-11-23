"""
E-Commerce Sales & Customer Retention Analysis
End-to-end: generate messy data -> clean (pandas) -> load to SQLite -> SQL analysis
-> charts/dashboard -> auto-generated insights report.
Run: python analysis.py   (needs pandas, numpy, matplotlib)
"""
import sqlite3, numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rng = np.random.default_rng(42)

# ---------- 1. RAW (MESSY) DATA ----------
def make_raw(n=6000):
    cats = ["Electronics", "Fashion", "Home", "Beauty", "Sports", "Books"]
    cities = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune"]
    df = pd.DataFrame({
        "order_id": np.arange(1, n + 1),
        "customer_id": rng.integers(1, 1201, n),
        "order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 600, n), "D"),
        "category": rng.choice(cats, n, p=[.25, .25, .18, .12, .1, .1]),
        "city": rng.choice(cities, n),
        "quantity": rng.integers(1, 5, n),
        "unit_price": rng.integers(150, 5000, n).astype(float),
        "status": rng.choice(["Delivered", "Cancelled", "Returned"], n, p=[.85, .09, .06]),
    })
    # inject dirt
    df["order_date"] = df["order_date"].dt.strftime("%Y-%m-%d")
    df.loc[rng.choice(n, 120, replace=False), "city"] = None
    df.loc[rng.choice(n, 100, replace=False), "unit_price"] = np.nan
    df.loc[rng.choice(n, 80, replace=False), "category"] = df["category"].str.lower()
    df.loc[rng.choice(n, 60, replace=False), "city"] = "  Bengaluru "
    df.loc[rng.choice(n, 15, replace=False), "quantity"] = -1
    return pd.concat([df, df.sample(70, random_state=1)], ignore_index=True)  # duplicates

# ---------- 2. CLEANING ----------
def clean(df):
    log = {"raw_rows": len(df)}
    df = df.drop_duplicates("order_id").copy();               log["duplicates_removed"] = log["raw_rows"] - len(df)
    df["category"] = df["category"].str.strip().str.title()
    df["city"] = df["city"].str.strip().fillna("Unknown")
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    log["invalid_quantity_removed"] = int((df["quantity"] <= 0).sum())
    df = df[df["quantity"] > 0]
    log["missing_price_imputed"] = int(df["unit_price"].isna().sum())
    df["unit_price"] = df["unit_price"].fillna(df.groupby("category")["unit_price"].transform("median"))
    df["revenue"] = df["quantity"] * df["unit_price"]
    df["order_month"] = df["order_date"].dt.to_period("M").astype(str)
    log["clean_rows"] = len(df)
    return df, log

# ---------- 3. SQL ANALYSIS ----------
QUERIES = {
"monthly_revenue": """
    SELECT order_month, ROUND(SUM(revenue)) AS revenue, COUNT(*) AS orders
    FROM orders WHERE status='Delivered' GROUP BY order_month ORDER BY order_month;""",
"category_performance": """
    SELECT category, ROUND(SUM(revenue)) AS revenue,
           ROUND(100.0*SUM(CASE WHEN status='Returned' THEN 1 ELSE 0 END)/COUNT(*),1) AS return_rate_pct
    FROM orders GROUP BY category ORDER BY revenue DESC;""",
"city_revenue": """
    SELECT city, ROUND(SUM(revenue)) AS revenue FROM orders
    WHERE status='Delivered' GROUP BY city ORDER BY revenue DESC;""",
"cohort_retention": """
    WITH first_o AS (SELECT customer_id, MIN(order_month) AS cohort FROM orders GROUP BY customer_id),
    act AS (SELECT DISTINCT customer_id, order_month FROM orders)
    SELECT f.cohort,
      (CAST(substr(a.order_month,1,4) AS INT)*12+CAST(substr(a.order_month,6,2) AS INT))
     -(CAST(substr(f.cohort,1,4) AS INT)*12+CAST(substr(f.cohort,6,2) AS INT)) AS month_n,
      COUNT(DISTINCT a.customer_id) AS customers
    FROM first_o f JOIN act a USING(customer_id) GROUP BY 1,2 ORDER BY 1,2;""",
"rfm": """
    SELECT customer_id,
           CAST(julianday((SELECT MAX(order_date) FROM orders)) - julianday(MAX(order_date)) AS INT) AS recency,
           COUNT(*) AS frequency, ROUND(SUM(revenue)) AS monetary
    FROM orders WHERE status='Delivered' GROUP BY customer_id;""",
"top_customers": """
    SELECT customer_id, ROUND(SUM(revenue)) AS revenue, COUNT(*) AS orders
    FROM orders WHERE status='Delivered' GROUP BY customer_id ORDER BY revenue DESC LIMIT 10;""",
}

def segment(r):
    if r.R >= 4 and r.F >= 4: return "Champions"
    if r.F >= 4: return "Loyal"
    if r.R >= 4: return "Recent"
    if r.R <= 2 and r.F <= 2: return "At Risk"
    return "Regular"

def main():
    raw = make_raw();  raw.to_csv("raw_orders.csv", index=False)
    df, log = clean(raw);  df.to_csv("clean_orders.csv", index=False)
    con = sqlite3.connect("orders.db"); df.to_sql("orders", con, if_exists="replace", index=False)
    res = {k: pd.read_sql(q, con) for k, q in QUERIES.items()}

    rfm = res["rfm"]
    rfm["R"] = pd.qcut(rfm.recency.rank(method="first"), 5, labels=[5,4,3,2,1]).astype(int)
    rfm["F"] = pd.qcut(rfm.frequency.rank(method="first"), 5, labels=[1,2,3,4,5]).astype(int)
    rfm["segment"] = rfm.apply(segment, axis=1)
    seg = rfm.groupby("segment").agg(customers=("customer_id","count"), revenue=("monetary","sum")).sort_values("revenue", ascending=False)

    ch = res["cohort_retention"].pivot(index="cohort", columns="month_n", values="customers")
    ret = ch.div(ch[0], axis=0) * 100

    # ---------- 4. DASHBOARD ----------
    fig, ax = plt.subplots(2, 3, figsize=(18, 10)); fig.suptitle("E-Commerce Performance Dashboard", fontsize=18, weight="bold")
    m = res["monthly_revenue"]
    ax[0,0].plot(m.order_month, m.revenue/1e6, marker="o", color="#4f46e5"); ax[0,0].set_title("Monthly Revenue (₹ M)")
    ax[0,0].tick_params(axis="x", rotation=90, labelsize=7)
    c = res["category_performance"]; ax[0,1].bar(c.category, c.revenue/1e6, color="#06b6d4"); ax[0,1].set_title("Revenue by Category (₹ M)")
    ax[0,1].tick_params(axis="x", rotation=30)
    ax[0,2].bar(c.category, c.return_rate_pct, color="#f43f5e"); ax[0,2].set_title("Return Rate % by Category"); ax[0,2].tick_params(axis="x", rotation=30)
    cy = res["city_revenue"]; ax[1,0].barh(cy.city[::-1], cy.revenue[::-1]/1e6, color="#10b981"); ax[1,0].set_title("Revenue by City (₹ M)")
    ax[1,1].bar(seg.index, seg.revenue/1e6, color="#f59e0b"); ax[1,1].set_title("Revenue by RFM Segment (₹ M)"); ax[1,1].tick_params(axis="x", rotation=30)
    im = ax[1,2].imshow(ret.iloc[:12, :9], cmap="Blues", aspect="auto"); ax[1,2].set_title("Cohort Retention % (first 12 cohorts)")
    ax[1,2].set_xlabel("Months since first order"); fig.colorbar(im, ax=ax[1,2])
    plt.tight_layout(); plt.savefig("dashboard.png", dpi=130)

    # ---------- 5. AUTO REPORT ----------
    best, worst = c.iloc[0], c.sort_values("return_rate_pct").iloc[-1]
    r1 = ret[1].mean(); chm = seg.loc["Champions"] if "Champions" in seg.index else None
    top10_share = res["top_customers"].revenue.sum() / res["rfm"].monetary.sum() * 100
    report = f"""# Insights & Recommendations
## Data quality
{log}

## Key findings
1. **Total delivered revenue:** ₹{m.revenue.sum()/1e6:.1f} M across {int(m.orders.sum()):,} orders.
2. **Top category:** {best.category} (₹{best.revenue/1e6:.1f} M). **Highest return rate:** {worst.category} ({worst.return_rate_pct}%).
3. **Top city:** {cy.iloc[0].city}.
4. **Month-1 retention averages {r1:.1f}%** – most new customers do not come back quickly.
5. **Top 10 customers = {top10_share:.1f}%** of revenue.
6. RFM: {int(seg.loc['At Risk','customers']) if 'At Risk' in seg.index else 0} customers are 'At Risk'.

## Recommendations
- Run a win-back campaign (email/discount) for 'At Risk' customers.
- Add a 2nd-order incentive within 30 days to lift month-1 retention.
- Investigate quality/size-guide issues in {worst.category} to cut returns.
- Increase inventory and ads in {cy.iloc[0].city} and {best.category}.
- Reward Champions with a loyalty programme.
"""
    open("insights_report.md", "w").write(report); print(report)

if __name__ == "__main__":
    main()
