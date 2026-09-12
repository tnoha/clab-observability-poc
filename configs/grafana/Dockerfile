FROM grafana/grafana:12.1.1
USER root
RUN mkdir -p /opt/grafana-plugins && chown grafana:root /opt/grafana-plugins
USER grafana
RUN grafana cli --pluginsDir /opt/grafana-plugins plugins install grafana-opensearch-datasource 2.34.3
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins
