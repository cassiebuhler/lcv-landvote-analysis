import ibis
from ibis import _
import ibis.expr.datatypes as dt  
import re
from cng.utils import *
from cng.h3 import *
from minio import Minio
import altair as alt

colors = {
    "dark_orange": "#ab5601",
    "light_orange": "#f3d3b1",
    "grey": "#d3d3d3",
    "light_green": "#c3dbc3",
    "dark_green": "#417d41",
    "dem_blue": "#1b46c2",
    "rep_red": "#E81B23"
}

def get_unique_rows(df):
    # collapse multi-county measures to one row per landvote_id
    unique_votes = (
        df
        .group_by("landvote_id")
        .agg(
            **{c: ibis._[c].first() for c in df.schema().names if c not in ("landvote_id", "county", "party")},
            # if spans multiple counties -> set different name for county
            county=ibis.ifelse(ibis._.county.nunique() > 1, "Multiple Counties", ibis._.county.first()), 
             # if counties differ in parties -> assign other label to party 
            party=ibis.ifelse(ibis._.party.nunique() > 1, "Mixed", ibis._.party.first()),
        )
    )
    return unique_votes


def get_passed(df):
    passed = df.filter(_.status.isin(["Pass", "Pass*"])).count().execute()
    total = df.count().execute()
    overall_passed = round(passed / total * 100, 2)
    print(f"{overall_passed}% Measures Passed from 1988 - 2024 \n")
    
## graphing utils 
LABEL_R = "Jurisdictions that voted Republican"
LABEL_D = "Jurisdictions that voted Democrat"

party_colors = alt.Scale(
    # domain=["Democrat", "Republican"],
    domain=[LABEL_D, LABEL_R],
    range=[colors["dem_blue"], colors["rep_red"]],
)


def year_line(df, y, group, title, y_title, stat='percent'):
    legend = alt.Legend(
        title=None,
        labelFontSize=14,
        labelLimit=500,
        orient='top',
        direction='horizontal',
        offset=5
    )

    if stat == 'percent':
        y_axis = alt.Axis(format="%", labelFontSize=14, titleFontSize=18)
    elif stat == 'count':
        y_axis = alt.Axis(format="d", labelFontSize=14, titleFontSize=18)
    else:
        y_axis = alt.Axis(
            format="$,.0f",
            labelExpr="datum.value / 1000000",
            labelFontSize=14,
            titleFontSize=18,
        )

    x_axis = alt.Axis(
        labelFontSize=14,
        titleFontSize=18,
        labelPadding=4,
        titlePadding=10,
        labelExpr="(toNumber(datum.value) % 2 === 0) ? datum.value : ''"
    )

    return (
        alt.Chart(df, title=alt.TitleParams(text=title, fontSize=20, dy=-5))
        .transform_calculate(
            party_label=(
                f"datum['{group}'] === 'Republican' ? '{LABEL_R}' : "
                f"datum['{group}'] === 'Democrat' ? '{LABEL_D}' : "
                f"datum['{group}']"
            )
        )
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year", axis=x_axis),
            y=alt.Y(f"{y}:Q", title=y_title, axis=y_axis),
            color=alt.Color("party_label:N", scale=party_colors, legend=legend),
        )
        .properties(width=800, height=160)
    )


def year_line_mechanism(df, y, group, title, y_title, stat='percent'):
    finance_colors = alt.Scale(
    # domain=['Property tax','Real estate transfer tax','Sales tax','Income tax','Tax (Other)'],
    # domain=["Tax","Bond","Other"],
    domain=["Tax","Bond"],

    range=[
        '#1f77b4',  # blue
        '#ff7f0e',  # orange
        # '#2ca02c',  # green
        # '#d62728',  # red
        # '#9467bd',  # purple
        # '#8c564b',  # brown
    ],
    )

    legend = alt.Legend(
        title=None,
        labelFontSize=14,
        labelLimit=500,
        orient='top',
        direction='horizontal',
        offset=5
    )

    if stat == 'percent':
        y_axis = alt.Axis(format="%", labelFontSize=14, titleFontSize=18)
    elif stat == 'count':
        y_axis = alt.Axis(format="d", labelFontSize=14, titleFontSize=18)
    else:
        y_axis = alt.Axis(
            format="$,.0f",
            labelExpr="datum.value / 1000000",
            labelFontSize=14,
            titleFontSize=18,
        )

    x_axis = alt.Axis(
        labelFontSize=14,
        titleFontSize=18,
        labelPadding=4,
        titlePadding=10,
        labelExpr="(toNumber(datum.value) % 2 === 0) ? datum.value : ''"
    )

    return (
        alt.Chart(df, title=alt.TitleParams(text=title, fontSize=20, dy=-5))
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year", axis=x_axis),
            y=alt.Y(f"{y}:Q", title=y_title, axis=y_axis),
            color=alt.Color("mechanism_group:N", scale=finance_colors, legend=legend),
            # color=alt.Color("finance_mechanism:N", scale=finance_colors, legend=legend),

        )
        .properties(width=800, height=160)
    )


def bar_chart(df, y, group, title, y_title, stat='percent'):
    legend = alt.Legend(
        title=None,
        labelFontSize=14,
        labelLimit=500,
        orient='top',
        direction='horizontal',
        offset=5
    )

    if stat == 'percent':
        y_axis = alt.Axis(format="%", labelFontSize=14, titleFontSize=18)
    elif stat == 'count':
        y_axis = alt.Axis(format="d", labelFontSize=14, titleFontSize=18)
    else:
        y_axis = alt.Axis(
            format="$,.0f",
            labelExpr="datum.value / 1000000",
            labelFontSize=14,
            titleFontSize=18,
        )

    x_axis = alt.Axis(
        labelFontSize=14,
        titleFontSize=18,
        labelPadding=4,
        titlePadding=10,
        # labelExpr="(toNumber(datum.value) % 2 === 0) ? datum.value : ''"
    )

    return (
        alt.Chart(df, title=alt.TitleParams(text=title, fontSize=20, dy=-5))
        .transform_calculate(
            party_label=(
                f"datum['{group}'] === 'Republican' ? '{LABEL_R}' : "
                f"datum['{group}'] === 'Democrat' ? '{LABEL_D}' : "
                f"datum['{group}']"
            )
        )
        .mark_line(point=True)
        .encode(
            x=alt.X("mechanism_type:O", title="Finance Mechanism", axis=x_axis),
            y=alt.Y(f"{y}:Q", title=y_title, axis=y_axis),
            color=alt.Color("party_label:N", scale=party_colors, legend=legend),
        )
        .properties(width=800, height=160)
    )

def bar_chart(df, y, group, title, y_title, stat='percent'):
    legend = alt.Legend(
        title=None,
        labelFontSize=14,
        labelLimit=500,
        orient='bottom',
        direction='horizontal',
        offset=5
    )

    if stat == 'percent':
        y_axis = alt.Axis(format="%", labelFontSize=14, titleFontSize=18)
    elif stat == 'count':
        y_axis = alt.Axis(format="d", labelFontSize=14, titleFontSize=18)
    else:
        y_axis = alt.Axis(
            format="$,.0f",
            labelExpr="datum.value / 1000000",
            labelFontSize=14,
            titleFontSize=18,
        )

    x_axis = alt.Axis(
        title=None,
        labels=False
    )
    col_header = alt.Header(
        title = None,
         labelFontSize=18,
    )

    return (
        alt.Chart(df, title=alt.TitleParams(text=title, fontSize=20, dy=-5))
        .transform_calculate(
            party_label=(
                f"datum['{group}'] === 'Republican' ? '{LABEL_R}' : "
                f"datum['{group}'] === 'Democrat' ? '{LABEL_D}' : "
                f"datum['{group}']"
            )
        )
        .mark_bar()
        .encode(
            column=alt.Column("mechanism_group:N", title="Finance Mechanism", header = col_header),
            y=alt.Y(f"{y}:Q", title=y_title, axis=y_axis),
            color=alt.Color("party_label:N", scale=party_colors, legend=legend),
            x=alt.X("party_label:N", title=None, sort=[LABEL_D, LABEL_R],axis=x_axis),

        )
        .properties(width=300, height=160)
    )

## graphing utils 
