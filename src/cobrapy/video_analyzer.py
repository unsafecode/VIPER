import json
import os
import time
import asyncio
import nest_asyncio
from typing import Any, Optional, Union, Type
from openai import AzureOpenAI, AsyncAzureOpenAI

from .azure_credentials import build_async_azure_credential, build_azure_credential
from .models.video import VideoManifest, Segment
from .models.environment import CobraEnvironment, GPTVision
from .analysis import AnalysisConfig
from .analysis.base_analysis_config import SequentialAnalysisConfig
from .cobra_utils import (
    encode_image_base64,
    validate_video_manifest,
    write_video_manifest,
)


_AZURE_OPENAI_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
_MAX_COMPLETION_TOKENS = 2000


def _get_completion_token_args(deployment: str) -> dict[str, int]:
    if "gpt-5" in deployment.lower():
        return {"max_completion_tokens": _MAX_COMPLETION_TOKENS}
    return {"max_tokens": _MAX_COMPLETION_TOKENS}


def _build_azure_openai_client_kwargs(
    vision_config: GPTVision,
) -> tuple[dict[str, Any], Any]:
    client_kwargs: dict[str, Any] = {
        "api_version": vision_config.api_version,
        "azure_endpoint": vision_config.endpoint,
    }

    if vision_config.api_key:
        client_kwargs["api_key"] = vision_config.api_key.get_secret_value()
        return client_kwargs, None

    from azure.identity import get_bearer_token_provider

    credential = build_azure_credential(
        managed_identity_client_id=vision_config.managed_identity_client_id
    )
    client_kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
        credential, _AZURE_OPENAI_TOKEN_SCOPE
    )
    return client_kwargs, credential


def _build_async_azure_openai_client_kwargs(
    vision_config: GPTVision,
) -> tuple[dict[str, Any], Any]:
    client_kwargs: dict[str, Any] = {
        "api_version": vision_config.api_version,
        "azure_endpoint": vision_config.endpoint,
    }

    if vision_config.api_key:
        client_kwargs["api_key"] = vision_config.api_key.get_secret_value()
        return client_kwargs, None

    from azure.identity.aio import get_bearer_token_provider

    credential = build_async_azure_credential(
        managed_identity_client_id=vision_config.managed_identity_client_id
    )
    client_kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
        credential, _AZURE_OPENAI_TOKEN_SCOPE
    )
    return client_kwargs, credential


class VideoAnalyzer:
    manifest: VideoManifest
    env: CobraEnvironment
    reprocess_segments: bool

    # take either a video manifest object or a path to a video manifest file
    def __init__(
        self, video_manifest: Union[str, VideoManifest], env: CobraEnvironment
    ):
        # get and validate video manifest
        self.manifest = validate_video_manifest(video_manifest)
        self.env = env
        self.latest_output_path: Optional[str] = None

    # Primary method to analyze the video
    def analyze_video(
        self,
        analysis_config: Type[AnalysisConfig],
        run_async=False,
        max_concurrent_tasks=None,
        reprocess_segments=False,
        **kwargs,
    ):

        self.reprocess_segments = reprocess_segments
        self.latest_output_path = None

        stopwatch_start_time = time.time()

        print(
            f'Starting video analysis: "{analysis_config.name}" for {self.manifest.name}'
        )

        # Analyze videos using the mapreduce sequence
        if analysis_config.analysis_sequence == "mapreduce":
            print(f"Populating prompts for each segment")

            self.generate_segment_prompts(analysis_config)

            if run_async:
                print("Running analysis asynchronously")
                nest_asyncio.apply()
                results_list = asyncio.run(
                    self._analyze_segment_list_async(
                        analysis_config, max_concurrent_tasks=max_concurrent_tasks
                    )
                )
            else:
                print("Running analysis.")
                results_list = self._analyze_segment_list(analysis_config)

        # For refine-style analyses that need to be run sequentially
        elif analysis_config.analysis_sequence == "refine":
            print(f"Analyzing segments sequentially with refinement")
            results_list = self._analyze_segment_list_sequentially(analysis_config)
        else:
            raise ValueError(
                f"You have provided an AnalyisConfig with a analysis_sequence that has not yet been implmented: {analysis_config.analysis_sequence}"
            )

        ## collapse the segment lists into one large list of segments. (Needed for expected ActionSummary format for UI)
        try:
            flattened_results = []
            for index, segment in enumerate(self.manifest.segments):
                if index >= len(results_list):
                    break

                segment_result = results_list[index]
                if isinstance(segment_result, list):
                    iterable = segment_result
                else:
                    iterable = [segment_result]

                for entry_position, elem in enumerate(iterable):
                    if isinstance(elem, dict):
                        enriched = dict(elem)
                        enriched.setdefault("_segment_index", index)
                        enriched.setdefault("_segment_name", segment.segment_name)
                        enriched.setdefault("_segment_start", segment.start_time)
                        enriched.setdefault("_segment_end", segment.end_time)
                        enriched.setdefault("_segment_entry_index", entry_position)
                        flattened_results.append(enriched)
                    else:
                        flattened_results.append(elem)

            final_results_output_path = os.path.join(
                self.manifest.processing_params.output_directory,
                f"_{analysis_config.name}.json",
            )

            # generate the final summary if enabled
            ## check to see if analysis_config has an attribute called run_final_summary

            results_payload = flattened_results

            if getattr(analysis_config, "run_final_summary", False):
                print(
                    f"Final summary of video analysis: {analysis_config.name} for {self.manifest.name}"
                )
                summary_prompt = self.generate_summary_prompt(
                    analysis_config, flattened_results
                )
                summary_results = self._call_llm(summary_prompt)

                self.manifest.final_summary = summary_results.choices[
                    0
                ].message.content

                results_payload = {
                    "final_summary": self.manifest.final_summary,
                    "results": flattened_results,
                }

            print(f"Writing results to {final_results_output_path}")

            self.latest_output_path = final_results_output_path

            with open(final_results_output_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(results_payload, indent=4))

            final_results = results_payload
        except:
            print(results_list)
            final_results_output_path = os.path.join(
                self.manifest.processing_params.output_directory,
                f"_video_analysis_results_{analysis_config.name}_errors.json",
            )
            self.latest_output_path = final_results_output_path
            with open(final_results_output_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(results_list, indent=4))
            raise ValueError(
                f"Bad data generated by model. Check the output at {final_results_output_path}"
            )

        stopwatch_end_time = time.time()

        elapsed_time = stopwatch_end_time - stopwatch_start_time

        print(
            f'Video analysis completed in {round(elapsed_time, 3)}: "{analysis_config.name}" for {self.manifest.name}'
        )
        # write the video manifest to the output directory
        write_video_manifest(self.manifest)
        return final_results

    def generate_segment_prompts(self, analysis_config: Type[AnalysisConfig]):
        for segment in self.manifest.segments:
            self._generate_segment_prompt(segment, analysis_config)

    def generate_summary_prompt(
        self, analysis_config: Type[AnalysisConfig], final_results
    ):
        messages = [
            {"role": "system", "content": analysis_config.summary_prompt},
            {"role": "user", "content": json.dumps(final_results)},
        ]
        return messages

    def _analyze_segment_list(
        self,
        analysis_config: Type[AnalysisConfig],
    ):
        results_list = []
        for segment in self.manifest.segments:
            parsed_response = self._analyze_segment(
                segment=segment, analysis_config=analysis_config
            )

            results_list.append(parsed_response)

        return results_list

    def _analyze_segment_list_sequentially(
        self, analysis_config: Type[SequentialAnalysisConfig]
    ):
        # if the analysis config is not a SequentialAnalysisConfig, raise an error
        if not isinstance(analysis_config, SequentialAnalysisConfig):
            raise ValueError(
                f"Sequential analysis can only be run with an obect that is a subclass of SequentialAnalysisConfig. You have provided an object of type {type(analysis_config)}"
            )

        # Start the timer
        stopwatch_start_time = time.time()

        results_list = []

        for i, segment in enumerate(self.manifest.segments):
            # check if the segment has already been analyzed, if so, skip it
            if (
                self.reprocess_segments is False
                and analysis_config.name in segment.analysis_completed
            ):
                print(
                    f"Segment {segment.segment_name} has already been analyzed, loading the stored value."
                )
                results_list.append(segment.analyzed_result[analysis_config.name])
                continue
            else:
                print(f"Analyzing segment {segment.segment_name}")

            messages = []
            number_of_previous_results_to_refine = (
                analysis_config.number_of_previous_results_to_refine
            )
            # generate the prompt for the segment
            # include the right number of previous results to refine and generate the prompt
            if len(results_list) == 0:
                result_list_subset = []
            elif number_of_previous_results_to_refine > 0:
                result_list_subset = results_list[
                    -number_of_previous_results_to_refine :
                ]
            else:
                result_list_subset = []

            result_list_subset_string = json.dumps(result_list_subset)

            # if it's the first segment, generate without the refine prompt; if it is not the first segment, generate with the refine prompt
            if i == 0:
                system_prompt_template = (
                    analysis_config.generate_system_prompt_template(
                        is_refine_step=False
                    )
                )

                system_prompt = system_prompt_template.format(
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    segment_duration=segment.segment_duration,
                    number_of_frames=segment.number_of_frames,
                    number_of_previous_results_to_refine=number_of_previous_results_to_refine,
                    video_duration=self.manifest.source_video.duration,
                    analysis_lens=analysis_config.lens_prompt,
                    results_template=analysis_config.results_template,
                    current_summary=result_list_subset_string,
                )
            else:
                system_prompt_template = (
                    analysis_config.generate_system_prompt_template(is_refine_step=True)
                )

                system_prompt = system_prompt_template.format(
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    segment_duration=segment.segment_duration,
                    number_of_frames=segment.number_of_frames,
                    number_of_previous_results_to_refine=number_of_previous_results_to_refine,
                    video_duration=self.manifest.source_video.duration,
                    analysis_lens=analysis_config.lens_prompt,
                    results_template=analysis_config.results_template,
                    current_summary=result_list_subset_string,
                )

            messages.append({"role": "system", "content": system_prompt})

            # Form the user prompt with the refine prompt, the audio transcription (if available), and the video frames
            user_content = []
            if segment.transcription is not None:
                user_content.append(
                    {
                        "type": "text",
                        "text": f"Audio Transcription for the next {segment.segment_duration} seconds: {segment.transcription}",
                    }
                )
            user_content.append(
                {
                    "type": "text",
                    "text": f"Next are the {segment.number_of_frames} frames from the next {segment.segment_duration} seconds of the video:",
                }
            )
            # Include the frames
            for i, frame in enumerate(segment.segment_frames_file_path):
                frame_time = segment.segment_frame_time_intervals[i]
                base64_image = encode_image_base64(frame)
                user_content.append(
                    {
                        "type": "text",
                        "text": f"Below is the frame at start_time {frame_time} seconds. Use this to provide timestamps and understand time.",
                    }
                )
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                            "detail": "low",
                        },
                    }
                )

            # add user content to the messages
            messages.append({"role": "user", "content": user_content})

            # write the prompt to the manifest
            prompt_output_path = os.path.join(
                segment.segment_folder_path, f"{segment.segment_name}_prompt.json"
            )

            with open(prompt_output_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(messages, indent=4))

            segment.segment_prompt_path = prompt_output_path

            # call the LLM to analyze the segment
            response = self._call_llm(messages)
            parsed_response = self._parse_llm_json_response(response)

            # append the result to the results list
            results_list.append(parsed_response)
            elapsed_time = time.time() - stopwatch_start_time
            print(
                f"Segment {segment.segment_name} analyzed in {round(elapsed_time, 2)} seconds."
            )

            # update the segment object with the analyzed results
            segment.analyzed_result[analysis_config.name] = parsed_response
            segment.analysis_completed.append(analysis_config.name)

            # update the manifest on disk (allows for checkpointing)
            write_video_manifest(self.manifest)

        elapsed_time = time.time() - stopwatch_start_time
        print(f"Analysis completed in {round(elapsed_time,2)} seconds.")

        return results_list

    async def _analyze_segment_list_async(
        self, analysis_config: Type[AnalysisConfig], max_concurrent_tasks=None
    ):
        if max_concurrent_tasks is None:
            max_concurrent_tasks = len(self.manifest.segments)
        else:
            max_concurrent_tasks = min(
                int(max_concurrent_tasks), len(self.manifest.segments)
            )

        sempahore = asyncio.Semaphore(max_concurrent_tasks)

        async def sem_task(segment):
            async with sempahore:
                return await self._analyze_segment_async(segment, analysis_config)

        async def return_value_task(segment):
            return segment.analyzed_result[analysis_config.name]

        segment_task_list = []

        for segment in self.manifest.segments:
            if (
                self.reprocess_segments is False
                and analysis_config.name in segment.analysis_completed
            ):
                print(
                    f"Segment {segment.segment_name} has already been analyzed, loading the stored value."
                )
                segment_task_list.append(return_value_task(segment))
                continue
            else:
                segment_task_list.append(sem_task(segment))

        results_list = await asyncio.gather(*segment_task_list)

        return results_list

    def _analyze_segment(
        self, segment: Segment, analysis_config: AnalysisConfig = None
    ):
        start_time = time.time()
        print(f"Starting analysis for segment {segment.segment_name}")

        # get the prompt to analyze the segment
        if segment.segment_prompt_path:
            with open(segment.segment_prompt_path, "r", encoding="utf-8") as f:
                segment_prompt = json.loads(f.read())
        else:
            segment_prompt = self._generate_segment_prompt(segment, analysis_config)

        # call the LLM to analyze the segment
        response = self._call_llm(segment_prompt)

        # parse the response and update the segment object
        parsed_response = self._parse_llm_json_response(response)
        segment.analyzed_result[analysis_config.name] = parsed_response
        segment.analysis_completed.append(analysis_config.name)

        # write the raw response outputs
        llm_response_output_path = os.path.join(
            segment.segment_folder_path, f"_segment_llm_response.json"
        )
        with open(llm_response_output_path, "w", encoding="utf-8") as f:
            f.write(response.model_dump_json(indent=4))

        # write the LLM generated analysis
        parsed_response_output_path = os.path.join(
            segment.segment_folder_path, f"_segment_analyzed_result.json"
        )
        with open(parsed_response_output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(parsed_response))

        endtime = time.time()
        elapsed_time = endtime - start_time
        print(
            f"Segment {segment.segment_name} analyzed in {round(elapsed_time, 3)} seconds"
        )

        return parsed_response

    async def _analyze_segment_async(
        self,
        segment: Segment,
        analysis_config: AnalysisConfig,
    ):

        start_time = time.time()
        print(f"Starting analysis for segment {segment.segment_name}")

        # Generate the prompt
        segment_prompt = self._generate_segment_prompt(segment, analysis_config)

        # submit call the LLM to analyze the segment
        response = await self._call_llm_async(segment_prompt)

        # parse the response and update the segment object
        parsed_response = self._parse_llm_json_response(response)
        segment.analyzed_result[analysis_config.name] = parsed_response
        segment.analysis_completed.append(analysis_config.name)

        # write the raw response outputs
        llm_response_output_path = os.path.join(
            segment.segment_folder_path, f"_segment_llm_response.json"
        )
        with open(llm_response_output_path, "w", encoding="utf-8") as f:
            f.write(response.model_dump_json(indent=4))

        # write the LLM generated analysis
        parsed_response_output_path = os.path.join(
            segment.segment_folder_path, f"_segment_analyzed_result.json"
        )
        with open(parsed_response_output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(parsed_response))

        endtime = time.time()
        elapsed_time = endtime - start_time
        print(
            f"Segment {segment.segment_name} analyzed in {round(elapsed_time, 3)} seconds"
        )

        return parsed_response

    def _generate_segment_refine_prompts(self, Segment, AnalysisConfig):
        pass

    def _generate_segment_prompt(
        self,
        segment: Segment,
        analysis_config: AnalysisConfig,
    ):
        print(f"Generating prompt for segment {segment.segment_name}")
        # populate the system prompt
        system_prompt_template = analysis_config.generate_system_prompt_template()
        system_prompt = system_prompt_template.format(
            start_time=segment.start_time,
            end_time=segment.end_time,
            segment_duration=segment.segment_duration,
            number_of_frames=segment.number_of_frames,
            video_duration=self.manifest.source_video.duration,
            analysis_lens=analysis_config.lens_prompt,
            results_template=analysis_config.results_template,
        )
        # populate the user prompt with alternating text describing the time of the frame
        # and the images themselves
        user_prompt_list = []

        # Provide the audio transcription for the segment
        if segment.transcription is not None:
            user_prompt_list.append(
                {
                    "type": "text",
                    "text": f'This is the audio transcription for the segment from {segment.start_time} to {segment.end_time}. TRANSCRIPTION START: \n"{segment.transcription}"\nTRANSCRIPTION END',
                }
            )

        for i, frame in enumerate(segment.segment_frames_file_path):
            frame_time = segment.segment_frame_time_intervals[i]
            # Include some text to explain the time frame
            user_prompt_list.append(
                {
                    "type": "text",
                    "text": f"Below is the frame at start_time {frame_time} seconds. Use this to provide timestamps and understand time",
                }
            )
            # Include the image
            base64_image = encode_image_base64(frame)
            user_prompt_list.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}",
                        "detail": "low",
                    },
                }
            )

        # create the messages payload in the OpenAI API format
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt_list},
        ]

        prompt_output_path = os.path.join(
            segment.segment_folder_path, f"{segment.segment_name}_prompt.json"
        )

        with open(prompt_output_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(messages, indent=4))

        segment.segment_prompt_path = prompt_output_path

        return messages

    def _call_llm(self, messages_list: list):
        vision_config = self.env.require_vision()

        client_kwargs, credential = _build_azure_openai_client_kwargs(vision_config)
        client = AzureOpenAI(**client_kwargs)
        try:
            response = client.chat.completions.create(
                model=vision_config.deployment,
                messages=messages_list,
                **_get_completion_token_args(vision_config.deployment),
            )
        finally:
            client.close()
            if credential is not None:
                credential.close()

        return response

    async def _call_llm_async(self, messages_list: list):
        vision_config = self.env.require_vision()

        client_kwargs, credential = _build_async_azure_openai_client_kwargs(
            vision_config
        )
        client = AsyncAzureOpenAI(**client_kwargs)
        try:
            response = await client.chat.completions.create(
                model=vision_config.deployment,
                messages=messages_list,
                **_get_completion_token_args(vision_config.deployment),
            )
        finally:
            await client.close()
            if credential is not None:
                await credential.close()

        return response

    def _parse_llm_json_response(self, response) -> dict:
        content = response.choices[0].message.content
        # remove the ```json from the beginning of response and ``` from the end
        content = content.replace("```json", "")
        content = content.replace("```", "")

        try:
            parsed_content = json.loads(content)
            return parsed_content
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {e}")
            print(f"Response: {content}")
            return content
