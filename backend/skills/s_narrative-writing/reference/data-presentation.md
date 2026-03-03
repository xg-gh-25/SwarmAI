# Data Presentation in Documents

This reference provides comprehensive guidance on how to effectively present data in documents. In a data-driven culture, the quality of data presentation directly impacts decision-making. These guidelines ensure that data is presented clearly, accurately, and persuasively, enabling readers to quickly grasp insights and make informed decisions.

## DOs

### Data Selection and Context
- Include only data that helps drive discussion and decision
- Provide context for all data points (baseline, comparison, benchmark)
- Present an absolute baseline when using relative metrics (e.g., "increased by 20% from 100 to 120 units")
- Explain any metrics that are not part of standard metrics packages
- Include timeframes for all data (e.g., "Q1 2025" rather than just "Q1")
- Show year-over-year (YoY) or period-over-period comparisons when possible
- Provide sample sizes or confidence intervals for survey data
- Include source information for all data presented
- Highlight significant patterns, outliers, or trends
- Connect data points to business implications and customer outcomes

### Chart Selection
- Choose the appropriate visualization type for your data:
  - Bar charts for comparing values across categories
  - Line charts for showing trends over time
  - Tables for presenting precise values and multiple dimensions
  - Scatter plots for showing relationships between two variables
  - Heat maps for showing patterns in complex data sets
  - Pie charts only for showing proportions with few categories (5 or fewer)
- Use the simplest chart type that effectively communicates the data
- Consider your audience's familiarity with different visualization types
- Use tables when exact numbers are important or when comparing across multiple dimensions
- Choose visualizations that reveal patterns and relationships in the data
- Select chart types that highlight the specific insight you want to convey

### Chart Design
- Include clear, descriptive titles that explain the insight
- Label all axes with units of measurement
- Use consistent abbreviations and terminology
- Include data source and time period information
- Add explanatory notes for complex visualizations
- Ensure sufficient contrast between colors
- Use patterns or labels in addition to color for accessibility
- Maintain consistent sizing of similar chart types
- Align related visualizations for easy comparison
- Size charts appropriately for the document format

### Data Integrity
- Present data accurately without distortion
- Use appropriate scales and axis ranges
- Start numeric axes at zero when comparing magnitudes
- Use consistent scales across comparable charts
- Disclose data sources and methodology
- Acknowledge limitations or caveats in the data
- Present both supporting and contradicting data points
- Distinguish between correlation and causation
- Include error bars or confidence intervals when appropriate
- Verify data accuracy before inclusion in documents

### Data Narrative
- Tell a story with your data that supports your main points
- Highlight the "so what" implications of each key data point
- Create a logical progression through multiple charts
- Use annotations to highlight key insights
- Connect visualizations to narrative elements
- Explain unexpected results or anomalies
- Provide sufficient explanation for complex data
- Draw clear conclusions from the data presented
- Link data insights to recommendations or next steps
- Balance quantitative data with qualitative insights when appropriate

## DON'Ts

### Data Selection Mistakes
- Cherry-pick data to support a predetermined narrative
- Present data without relevant benchmarks or comparisons
- Omit important caveats or limitations
- Include data that doesn't support your main points
- Present too many metrics without clear prioritization
- Use vanity metrics that look good but don't drive decisions
- Mix incomparable time periods or metrics
- Present data without explaining its significance
- Include unnecessary precision that doesn't add value
- Overwhelm readers with excessive data points

### Misleading Representations
- Use truncated axes that exaggerate differences
- Create 3D charts that distort proportions
- Use area to represent one-dimensional data
- Compare incomparable time periods or metrics
- Manipulate scales to exaggerate or minimize changes
- Use misleading color progressions that imply judgment
- Present correlation as causation
- Cherry-pick starting or ending points to show desired trends
- Use cumulative graphs when period-by-period would be clearer
- Create visualizations that require extensive explanation

### Poor Design Choices
- Include too many data series in a single chart
- Use complex chart types when simpler ones would suffice
- Add unnecessary decorative elements
- Create visualizations that require extensive explanation
- Try to show too many dimensions in one visualization
- Use colors that are indistinguishable for colorblind users
- Create charts with insufficient contrast
- Use font sizes that are too small to read
- Overcrowd charts with excessive labels
- Use inconsistent scales across comparable charts

### Accessibility Failures
- Rely solely on color to convey information
- Use red/green color combinations without additional indicators
- Create charts with poor contrast ratios
- Use patterns that create visual vibration
- Omit alternative text descriptions for digital documents
- Create visualizations that don't work when printed in black and white
- Use font sizes below 8pt in charts
- Create overly complex visualizations that are difficult to interpret
- Fail to provide text alternatives for key data points
- Use color schemes that don't account for color vision deficiencies

### Narrative Disconnects
- Present data without connecting to the document's main points
- Include visualizations without referencing them in the text
- Fail to explain the significance of data patterns
- Present contradictory data without explanation
- Include charts that don't support your conclusions
- Overwhelm narrative with excessive charts and graphs
- Fail to draw clear conclusions from presented data
- Present data without actionable insights
- Include visualizations that distract from the main message
- Create a disconnect between data presentation and recommendations

## Examples

### Good Example: Effective Data Presentation

```
## 2. Customer Satisfaction Analysis

Our analysis of customer satisfaction metrics reveals three significant trends that inform our recommendation:

### 2.1 Overall Satisfaction Trend

Customer satisfaction has declined 12 percentage points over the past year, with the most significant drop occurring in Q4 2024.

[Chart: Line chart showing customer satisfaction percentage by quarter from Q1 2024 to Q1 2025, with clear downward trend]

Figure 1: Customer Satisfaction Score (0-100), Quarterly Average
Source: Post-purchase surveys, n=2,500 responses per quarter

### 2.2 Satisfaction by Customer Segment

The decline is not uniform across customer segments. Our highest-value customers (Premium tier) show the steepest decline (-18 points), while new customers show only a modest decrease (-5 points).

| Customer Segment | Q1 2024 | Q1 2025 | Change |
|------------------|---------|---------|--------|
| Premium tier     | 87      | 69      | -18    |
| Standard tier    | 82      | 73      | -9     |
| Basic tier       | 78      | 74      | -4     |
| New customers    | 80      | 75      | -5     |

Table 1: Customer Satisfaction Score by Segment (0-100)
Source: Post-purchase surveys, Q1 2024 (n=2,450) vs. Q1 2025 (n=2,500)

### 2.3 Key Drivers of Dissatisfaction

Analysis of customer feedback reveals three primary pain points driving the decline:

[Chart: Bar chart showing percentage of negative feedback by category]

Figure 2: Percentage of Negative Feedback by Category, Q1 2025
Source: Customer feedback analysis, n=875 negative comments

The checkout experience accounts for 58% of all negative feedback, with specific issues centered around:
- Payment processing time (average 8.2 seconds, 60% longer than industry benchmark)
- Form validation errors (affecting 23% of transactions)
- Mobile responsiveness issues (31% of mobile users report difficulties)

This data suggests that addressing the checkout experience, particularly for Premium tier customers, should be our highest priority for improving overall satisfaction.
```

### Bad Example: Poor Data Presentation

```
2. Customer Satisfaction

Customer satisfaction has gone down. Here's the data:

Q1 2024: 82%
Q2 2024: 80%
Q3 2024: 77%
Q4 2024: 72%
Q1 2025: 70%

Different customer types have different scores:
Premium: 69%
Standard: 73%
Basic: 74%
New: 75%

Premium customers used to be at 87% so they're much less happy now.

Customers don't like the checkout experience. 58% of complaints are about checkout. Payment is slow and there are form validation problems. Mobile also has issues.

We should fix the checkout experience to make customers happier.
```

### Good Example: Effective Chart with Context

```
## 3.2 Performance Impact Analysis

Our A/B test of the new checkout flow demonstrates significant performance improvements across key metrics:

[Chart: Bar chart comparing old vs. new checkout flow across multiple metrics]

Figure 3: Performance Comparison: Current vs. New Checkout Flow
Sample: 50,000 transactions per variant, Feb 15-28, 2025
Statistical significance: p < 0.001 for all metrics shown

The new checkout flow delivers substantial improvements:
- 62% reduction in cart abandonment rate (from 24% to 9%)
- 73% decrease in payment processing time (from 8.2s to 2.2s)
- 86% reduction in form validation errors (from 23% to 3.2% of transactions)

These improvements are consistent across device types and customer segments, with mobile users seeing the largest relative gains (92% reduction in reported difficulties).

The performance improvements translate to an estimated $4.2M in additional annual revenue based on:
- 2.5M monthly checkout attempts
- $85 average order value
- 15% net reduction in abandonment rate

This data strongly supports our recommendation to implement the new checkout flow across all customer segments by the end of Q2 2025.
```

### Bad Example: Poor Chart with Insufficient Context

```
3.2 Performance Results

Here's how the new checkout performed:

[Chart: Bar chart showing percentages for different metrics without clear labels]

As you can see, the new checkout is much better. Cart abandonment went down a lot and it's faster too. Users had fewer problems with forms.

This will increase our revenue significantly when we roll it out.
```

## Rationale

Effective data presentation in documents is critical for several reasons:

1. **Data-Driven Decision Making**: A strong writing culture emphasizes decisions based on data rather than opinions. Clear data presentation ensures decision-makers have the information they need in a format they can quickly understand.

2. **Cognitive Efficiency**: Well-presented data reduces cognitive load, allowing readers to grasp insights quickly without struggling to interpret complex information.

3. **Persuasive Communication**: Properly presented data strengthens arguments and recommendations, making documents more persuasive and effective.

4. **Intellectual Honesty**: Transparent data presentation that acknowledges limitations and presents balanced information supports the principle of earning trust.

5. **Meeting Effectiveness**: The practice of starting meetings with document reading requires data to be presented in ways that can be quickly understood during limited reading time.

6. **Accessibility**: Properly designed data visualizations ensure information is accessible to all readers, including those with visual impairments or color vision deficiencies.

7. **Time Efficiency**: Clear data presentation respects readers' time by making insights immediately apparent rather than requiring extensive analysis.

8. **Bias Mitigation**: Standardized approaches to data presentation help reduce cognitive biases that can affect interpretation and decision-making.

9. **Customer Obsession**: Effective presentation of customer data ensures customer perspectives remain central to discussions and decisions.

10. **Scalable Insights**: Well-presented data can be understood by people beyond the immediate audience, allowing insights to scale across the organization.

In the six-page narrative format, every chart and data point must earn its place by providing clear value to the reader. Effective data presentation transforms raw information into actionable insights that drive better decisions and outcomes.
