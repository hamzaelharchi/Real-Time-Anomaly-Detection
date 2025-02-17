import onnxruntime as ort
import numpy as np
import pandas as pd
import json
from pyspark.context import SparkContext
from pyspark.sql.session import SparkSession
from pyspark.sql.functions import col
import joblib

# Initialize Spark
sc = SparkContext('local')
spark = SparkSession(sc)
spark.sparkContext.setLogLevel("ERROR")

# Kafka topic and bootstrap servers
TOPIC_NAME = 'processed_swat'
BOOTSTRAP_SERVERS = "kafka:29092"

print(f"Initializing Kafka Consumer for topic '{TOPIC_NAME}' at '{BOOTSTRAP_SERVERS}'")

# Read from Kafka with Structured Streaming
df = spark \
    .readStream \
    .format("kafka") \
    .option("kafka.bootstrap.servers", BOOTSTRAP_SERVERS) \
    .option("subscribe", TOPIC_NAME) \
    .option("startingOffsets", "latest") \
    .load()

print("Kafka Stream initialized successfully.")

# Convert key and value to strings
df1 = df.selectExpr("CAST(key AS STRING)", "CAST(value AS STRING)")
print("Data from Kafka successfully cast to strings.")

# Global buffer to accumulate rows
global_buffer = []

# LSTM Autoencoder expects 10 timesteps, initialize as global constant
TIMESTEPS = 10

# Load ONNX LSTM Autoencoder model
print("Loading ONNX model...")
myAutoEncoder = ort.InferenceSession("work/Autoencoder/model.onnx")
input_name = myAutoEncoder.get_inputs()[0].name  # Get input tensor name
output_name = myAutoEncoder.get_outputs()[0].name  # Get output tensor name
print(f"Model loaded successfully. Input tensor name: {input_name}, Output tensor name: {output_name}")

# Function to process each batch
def process_batch(batch_df, batch_id):
    global global_buffer
    print(f"Processing batch {batch_id}...")

    # Convert Spark DataFrame to Pandas DataFrame
    pandas_df = batch_df.toPandas()
    print(f"Batch {batch_id} converted to Pandas DataFrame with {len(pandas_df)} rows.")

    # Parse and extract numeric data points from Kafka value
    for index, row in pandas_df.iterrows():
        try:
            # Parse the value field (adjust parsing based on Kafka message structure)
            data_point = json.loads(row['value'])
            print(f"Data point {index} parsed: {data_point}")

            # Check if data_point is a dictionary and extract numeric values
            if isinstance(data_point, dict):
                numeric_values = list(data_point.values())
            elif isinstance(data_point, list):
                numeric_values = data_point
            else:
                numeric_values = [data_point]

            # Validate the number of features (ensure 69 features per point)
            if len(numeric_values) == 69:
                global_buffer.append(numeric_values)
                print(f"Valid data point added to buffer: {numeric_values}")
            else:
                print(f"Skipping invalid data point (unexpected number of features): {numeric_values}")
        except Exception as e:
            print(f"Error parsing data point: {e}")

    print(f"Global buffer size: {len(global_buffer)}")

    # Process data when the buffer has at least 10 data points
    while len(global_buffer) >= TIMESTEPS:
        print("Sufficient data in buffer. Preparing batch for inference...")
        # Extract the first 10 data points
        batch = global_buffer[:TIMESTEPS]
        global_buffer = global_buffer[TIMESTEPS:]  # Remove used data points

        # Convert to NumPy array and reshape for LSTM input (1, 10, 69)
        input_data = np.array(batch, dtype=np.float32).reshape(1, TIMESTEPS, 69)
        print(f"Input data prepared for model inference. Shape: {input_data.shape}")

        # Run the ONNX model
        try:
            reconstructed = myAutoEncoder.run([output_name], {input_name: input_data})
            # Squeeze the output to remove any singleton dimensions
            reconstructed_squeezed = np.squeeze(reconstructed[0])
            print(f"Reconstructed output shape after squeezing: {reconstructed_squeezed.shape}")
        except Exception as e:
            print(f"Error during ONNX model inference: {e}")
# Write stream with foreachBatch for custom processing
print("Starting Structured Streaming query...")
query = df1 \
    .writeStream \
    .outputMode("append") \
    .format("console") \
    .foreachBatch(process_batch) \
    .start()

print("Structured Streaming query started. Awaiting termination...")
query.awaitTermination()
