{% extends "base.html" %}

{% block content %}
<div class="card">
  <h2>{{ player }} — {{ my_team }}</h2>
  {% if summary %}
  <p>
    <span class="pill">GP {{ summary.gp }}</span>
    <span class="pill">MPG {{ "%.1f"|format(summary.mpg) }}</span>
    <span class="pill">PPG {{ "%.1f"|format(summary.ppg) }}</span>
    <span class="pill">BPR {{ "%.2f"|format(summary.bpr) }}</span>
  </p>
  {% endif %}
  <p><a href="/season">← Back to season dashboard</a></p>
</div>

{% if summary %}
<div class="card">
  <h2>Season Summary</h2>
  <table>
    <tr>
      <th>True OBPR</th><th>True DBPR</th><th>BPR</th>
      <th>On OffEff</th><th>Off OffEff</th><th>Off Impact</th>
      <th>On DefEff</th><th>Off DefEff</th><th>Def Impact</th>
      <th>Net On/Off</th>
    </tr>
    <tr>
      <td>{{ "%.2f"|format(summary.obpr) }}</td>
      <td>{{ "%.2f"|format(summary.dbpr) }}</td>
      <td><strong>{{ "%.2f"|format(summary.bpr) }}</strong></td>
      <td>{{ "%.1f"|format(summary.on_off_eff) }}</td>
      <td>{{ "%.1f"|format(summary.off_off_eff) }}</td>
      <td>{{ "%.2f"|format(summary.off_impact) }}</td>
      <td>{{ "%.1f"|format(summary.on_def_eff) }}</td>
      <td>{{ "%.1f"|format(summary.off_def_eff) }}</td>
      <td>{{ "%.2f"|format(summary.def_impact) }}</td>
      <td>{{ "%.1f"|format(summary.net_onoff) }}</td>
    </tr>
  </table>
</div>
{% endif %}

<div class="card">
  <h2>Game Log</h2>
  <table>
    <tr>
      <th>Game</th><th>Opponent</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>TO</th>
      <th>True OBPR</th><th>True DBPR</th><th>BPR</th>
      <th>On Net</th>
    </tr>
    {% for s in stats %}
    {% set g = game_by_id.get(s.game_id) %}
    {% set i = impact_by_game.get(s.game_id) %}
    <tr>
      <td><a href="/games/{{ s.game_id }}">Game {{ s.game_id }}</a></td>
      <td>
        {% if g %}
          {% if g.away_team == my_team %}{{ g.home_team }}{% else %}{{ g.away_team }}{% endif %}
        {% endif %}
      </td>
      <td>{{ s.minutes }}</td>
      <td>{{ s.pts }}</td>
      <td>{{ s.reb }}</td>
      <td>{{ s.ast }}</td>
      <td>{{ s.tov }}</td>
      <td>{{ "%.2f"|format(s.obpr) }}</td>
      <td>{{ "%.2f"|format(s.dbpr) }}</td>
      <td><strong>{{ "%.2f"|format(s.bpr) }}</strong></td>
      <td>
        {% if i %}
          {{ "%.1f"|format(i.on_off_eff - i.on_def_eff) }}
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
</div>
{% endblock %}
