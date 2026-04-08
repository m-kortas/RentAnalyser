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
import datetime
import logging
logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.INFO)

st.cache_data.clear()



def download_bond_data():
    url = "https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data"
    print(f"Requesting data from: {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an exception for bad status codes
        soup = BeautifulSoup(response.content, 'html.parser')
    except Exception as e:
        print(f"Error fetching the webpage: {e}")
        return

    bond_lodgements_section = soup.find('h2', string='Bond lodgements')
    if bond_lodgements_section:
        first_link = bond_lodgements_section.find_next('a', href=re.compile(r'\.xlsx$'))
        if first_link:
            # Fix URL construction
            href = first_link['href']
            if href.startswith('http'):
                file_url = href  # Use the full URL if it's already absolute
            elif href.startswith('//'):
                file_url = 'https:' + href  # Add protocol if it's protocol-relative
            else:
                # Make sure the href doesn't start with a slash if we're adding the domain
                if href.startswith('/'):
                    file_url = "https://www.nsw.gov.au" + href
                else:
                    file_url = "https://www.nsw.gov.au/" + href
            
            print(f"Attempting to download from URL: {file_url}")
            file_name = first_link.text.strip()

            try:
                file_response = requests.get(file_url)
                if file_response.status_code == 200:
                    if not os.path.exists('downloads'):
                        os.makedirs('downloads')

                    file_path = os.path.join('downloads', file_name)
                    with open(file_path, 'wb') as file:
                        file.write(file_response.content)
                    print(f"Downloaded: {file_name}")
                else:
                    print(f"Failed to download the file. Status code: {file_response.status_code}")
            except Exception as e:
                print(f"Error downloading file: {e}")
        else:
            print("No XLSX link found in the Bond lodgements section")
    else:
        print("Bond lodgements section not found")

 

def download_latest_rental_bond_data():
    current_datetime = datetime.datetime.now()
    current_day = current_datetime.day
    
    last_run_file = "last_run.txt"
    
    # Check if we've already run today
    if os.path.exists(last_run_file):
        with open(last_run_file, "r") as file:
            content = file.read().strip()
            # Only try to convert to int if the file has content
            if content:
                try:
                    last_run_day = int(content)
                    if last_run_day == current_day:
                        print("Script already ran today. No download will occur.")
                        return
                except ValueError:
                    # If file content is invalid, treat it as if file doesn't exist
                    print("Invalid last run date found, will proceed with check")
            else:
                print("Empty last run file found, will proceed with check")
    

    download_bond_data()
        
        # Update the last run file with just the day
    with open(last_run_file, "w") as file:
            file.write(str(current_day))
   # else:
      #  print(f"Current day is {current_day}, waiting for day 12 to download new data")

download_latest_rental_bond_data()
st.set_page_config(page_title="Explore Sydney's Latest Rental Trends")
st.cache_data.clear() 



def get_newest_file():
    # Get all files in the 'downloads' directory
    files = [f for f in os.listdir('downloads') if os.path.isfile(os.path.join('downloads', f))]

    # Log files for debugging
    logging.info(f"Files in directory: {files}")

    # Regex to extract year and month from the file name
    date_pattern = re.compile(r"(\bJanuary|\bFebruary|\bMarch|\bApril|\bMay|\bJune|\bJuly|\bAugust|\bSeptember|\bOctober|\bNovember|\bDecember)\s(\d{4})")

    def extract_year_month(file_name):
        match = date_pattern.search(file_name)
        if match:
            month_str, year = match.groups()
            # Convert month name to a number (e.g., January -> 1)
            month_num = {
                "January": 1, "February": 2, "March": 3, "April": 4,
                "May": 5, "June": 6, "July": 7, "August": 8,
                "September": 9, "October": 10, "November": 11, "December": 12
            }[month_str]
            return int(year), month_num
        return 0, 0  # Default for files without a date

    # Find the newest file based on extracted year and month
    newest_file = max(files, key=lambda f: extract_year_month(f))

    # Log the selected file
    logging.info(f"Newest File (by year and month): {newest_file}")
    return newest_file



    
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

    print(data)
     
    bonds = pd.read_excel(data,
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
    
    # Calculate both median rent and count of properties
    grouped = filtered_bonds.groupby('Postcode', observed=True).agg({
        'Weekly Rent': ['median', 'count']
    }).reset_index()
    
    # Flatten column names
    grouped.columns = ['Postcode', 'Median_Weekly_Rent', 'Property_Count']
    
    return pd.merge(Sydney_area_postcode, grouped, on='Postcode', how='inner')

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
    geometry = gpd.GeoSeries.from_wkt(merged_data['geometry'].astype(str))
    merged_data = gpd.GeoDataFrame(
        merged_data.drop(columns=['geometry']),
        geometry=geometry
    )
    
    # Now you can safely apply the GeoJSON conversion
    merged_data['Geolocation'] = merged_data['geometry'].apply(
        lambda x: json.loads(gpd.GeoSeries([x]).to_json())['features'][0]['geometry']
    )
    
    return merged_data[['Name', 'Median_Weekly_Rent', 'Property_Count', 'Geolocation']]

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
        data=gdf[['Name', 'Median_Weekly_Rent', 'Property_Count', 'lat', 'lon', 'color']],
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
            "html": "<b>Suburb:</b> {Name} <br/>" + 
                   "<b>Median Weekly Rent (Postcode):</b> ${Median_Weekly_Rent}<br/>" +
                   "<b>Available Properties (Postcode):</b> {Property_Count}",
            "style": {"backgroundColor": "steelblue", "color": "white"}
        }
    )


st.title("Explore Sydney's Latest Rental Trends")

st.markdown("""
This app provides real rental data sourced directly from the **NSW Government**, ensuring the most up-to-date and reliable information available.

Rental data typically reflects the previous month, as it is collected at the **start of each month**. The app updates automatically every month with the latest government data, when available.

If data for a specific suburb is missing, it means no properties of that type were rented in that area during the previous month.

The median price and the number of available properties are calculated at the **postcode level**.
""")

bonds_data = get_newest_file()
cleaned_text = re.sub(r"Rental bond lodgement data -\s*|\(.*?\)", "", bonds_data).strip()

st.markdown(f"""
Currently displaying: **{cleaned_text}**
""")


bonds_path = os.path.join('downloads',bonds_data)
gdf, Sydney_area_postcode, bonds = download_data(bonds_path)

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

logging.info(f"User searched with Bedrooms: {selected_bedrooms}, Dwelling Types: {selected_dwelling}")

with st.spinner('Updating visualization...'):
    filtered_data = get_data(Sydney_area_postcode, bonds, selected_bedrooms, selected_dwelling)
    merged_df = process_geojson_data(gdf, filtered_data)
    
    if merged_df.empty:
        st.write("No properties found for the selected filters.")
    else:
        try:
            map_object = create_map(merged_df)
            if map_object:
                st.pydeck_chart(map_object)
        except Exception as ex:
            st.write(f"An error occurred while creating the map: {ex}")
            print(f"Error: {ex}")

st.markdown("""
The map uses color coding to represent different rental price ranges:

- **Sky Blue**: Rent prices up to the 25th percentile (lower range).
- **Light Blue**: Rent prices between the 25th and 50th percentiles (moderate range).
- **Yellow**: Rent prices between the 50th percentile and 75th percentile(higher middle range).
- **Red**: Rent prices above 75th percentile (premium range).

These color gradients help visualize the distribution of rental prices across Sydney, making it easier to identify areas with lower or higher rent prices.
""")

st.markdown("""
Created by a Data Scientist **Magdalena Kortas**. Feel free to connect with me on [LinkedIn](https://www.linkedin.com/in/mkortas/).
""")

st.markdown("""
©RentAnalyser, 2024. All rights reserved.  Unauthorized reproduction or distribution of its contents is prohibited.  
""")


