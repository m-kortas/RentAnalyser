import geopandas as gpd
import os
import numpy as np
import json
import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import shape, Point
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import datetime

st.set_page_config(page_title="Explore Sydney's Latest Rental Trends")

@st.cache_data
def get_newest_file():
    files = [f for f in os.listdir('downloads') if os.path.isfile(os.path.join('downloads', f))]
    newest_file = max(files, key=lambda f: os.path.getmtime(os.path.join('downloads', f)))
    return os.path.join('downloads', newest_file)

@st.cache_data
def download_data(data):
    # Pre-process GeoJSON and store as GeoDataFrame for faster processing
    gdf = pd.read_csv('geo_data.csv')
    gdf['nsw_loca_2'] = gdf['nsw_loca_2'].str.title()
    
    Sydney_area_postcode = pd.read_csv('sydney_d.csv')
    Sydney_area_postcode['Name'] = Sydney_area_postcode['Name'].str.title()
    
    dtype_dict = {
        'Postcode': 'category',
        'Bedrooms': 'category',
        'Dwelling Type': 'category',
        'Weekly Rent': 'object'
    }
    
     
    bonds = pd.read_excel(
        'downloads/Rental bond lodgement data - November 2024 (XLSX 693.72KB)',
        header=2,
        usecols=['Postcode', 'Bedrooms', 'Dwelling Type', 'Weekly Rent'],
        engine='openpyxl'
    )

    
    return gdf, Sydney_area_postcode, bonds

@st.cache_data
def get_data(Sydney_area_postcode, bonds, bedrooms, dwelling):
    # Filter data efficiently using boolean indexing
    mask = (bonds['Bedrooms'] == bedrooms) & \
           (bonds['Weekly Rent'] != 'U') & \
           (bonds['Dwelling Type'].isin(dwelling))
    
    filtered_bonds = bonds[mask].copy()
    filtered_bonds['Weekly Rent'] = pd.to_numeric(filtered_bonds['Weekly Rent'], errors='coerce')
    
    # Use more efficient groupby operation
    prices = filtered_bonds.groupby('Postcode', observed=True)['Weekly Rent'].median().reset_index()
    prices.rename(columns={'Weekly Rent': 'Median_Weekly_Rent'}, inplace=True)
    
    return pd.merge(Sydney_area_postcode, prices, on='Postcode', how='inner')

@st.cache_data
def process_geojson_data(_gdf, postcode_data):
    # Perform spatial join using GeoDataFrame
    merged_data = pd.merge(
        postcode_data,
        _gdf,
        left_on='Name',
        right_on='nsw_loca_2',
        how='inner'
    )
    if merged_data['geometry'].dtype == 'object':
        merged_data['geometry'] = gpd.GeoSeries.from_wkt(merged_data['geometry'])

    # Ensure 'geometry' is recognized as valid geometry objects
    merged_data = gpd.GeoDataFrame(merged_data, geometry='geometry')
    
    # Check if the conversion worked
    print(merged_data['geometry'].head())  # Check if the conversion was successful
    
    # Now you can safely apply the GeoJSON conversion
    merged_data['Geolocation'] = merged_data['geometry'].apply(
        lambda x: json.loads(gpd.GeoSeries([x]).to_json())['features'][0]['geometry']
    )

    
    return merged_data[['Name', 'Median_Weekly_Rent', 'Geolocation']]

@st.cache_data
@st.cache_data
def create_map(merged_df):
    if merged_df.empty:
        st.write("No properties available for the selected filters.")
        return None
    
    gdf = gpd.GeoDataFrame(
        merged_df,
        geometry=[shape(geo) for geo in merged_df['Geolocation']]
    )
    
    gdf['centroid'] = gdf.geometry.centroid
    gdf['lon'] = gdf.centroid.x
    gdf['lat'] = gdf.centroid.y
    
    q25 = gdf['Median_Weekly_Rent'].quantile(0.25)
    q50 = gdf['Median_Weekly_Rent'].quantile(0.50)
    q75 = gdf['Median_Weekly_Rent'].quantile(0.75)
    
    def get_colors(rents):
        colors = np.empty((len(rents), 4), dtype=np.uint8)
        colors[rents <= q25] = [30, 144, 255, 180]
        colors[(rents > q25) & (rents <= q50)] = [80, 200, 220, 180]
        colors[(rents > q50) & (rents < q75)] = [220, 180, 100, 180]
        colors[rents >= q75] = [255, 80, 50, 180]
        return colors.tolist()

    gdf['color'] = get_colors(gdf['Median_Weekly_Rent'].values)
    
    view_state = pdk.ViewState(
        latitude=-33.8688,
        longitude=151.2093,
        zoom=10,
        pitch=45,
        bearing=0
    )
    
    column_layer = pdk.Layer(
        "ColumnLayer",
        data=gdf[['Name', 'Median_Weekly_Rent', 'lat', 'lon', 'color']],
        get_position=['lon', 'lat'],
        get_elevation='Median_Weekly_Rent',
        elevation_scale=2,
        radius=150,
        get_fill_color='color',
        pickable=True,
        auto_highlight=True
    )
    
    return pdk.Deck(
        layers=[column_layer],
        initial_view_state=view_state,
        tooltip={
            "html": "<b>Suburb:</b> {Name}<br/><b>Median Weekly Rent:</b> ${Median_Weekly_Rent}",
            "style": {"backgroundColor": "steelblue", "color": "white"}
        }
    )


st.title("Explore Sydney's Latest Rental Trends")

st.markdown("""
This app presents real rental data sourced directly from **NSW Government**, the most up-to-date and reliable source available. 
The data is generally one month behind, as it reflects rental information collected at the start of each month for the previous month.
""")

st.markdown("""
The map uses color coding to represent different rental price ranges:

- **Light Blue**: Rent prices up to the 25th percentile (lower range).
- **Sky Blue**: Rent prices between the 25th and 50th percentiles (moderate range).
- **Yellow**: Rent prices between the 50th percentile and 75th percentile(higher middle range).
- **Red**: Rent prices above 75th percentile (premium range).

These color gradients help visualize the distribution of rental prices across Sydney, making it easier to identify areas with lower or higher rent prices.
""")

with st.spinner('Loading...'):
    bonds_data = get_newest_file()
    gdf, Sydney_area_postcode, bonds = download_data(bonds_data)

bedroom_options = ['0', '1', '2', '3', '4', '5']
dwelling_options = ['H', 'T', 'F']
default_bedrooms = '2'
default_dwelling = ['F']

selected_bedrooms = st.selectbox(
    "Select Number of Bedrooms:",
    bedroom_options,
    index=bedroom_options.index(default_bedrooms)
)
selected_dwelling = st.multiselect(
    "Select Dwelling Type (H-House, T-Townhouse, F-Flat):",
    dwelling_options,
    default=default_dwelling
)

with st.spinner('Updating visualization...'):
    filtered_data = get_data(Sydney_area_postcode, bonds, selected_bedrooms, selected_dwelling)
    merged_df = process_geojson_data(gdf, filtered_data)
    map_object = create_map(merged_df)
    
    if map_object is not None:
        st.pydeck_chart(map_object)


st.markdown("""
Created by a Data Scientist **Magdalena Kortas**. Feel free to connect with me on [LinkedIn](https://www.linkedin.com/in/mkortas/).
""")
