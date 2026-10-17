"""JSON output schema for the maps_lookup_json agent."""

from typing import Optional

from pydantic import BaseModel, Field


class PlaceDetails(BaseModel):
    found: bool = Field(description="False if this place could not be found or grounding failed")
    name: str = Field(description="The place's name as given/searched, even if not found")
    address: Optional[str] = Field(default=None, description="Full street address, or null")
    rating: Optional[float] = Field(default=None, description="Star rating out of 5, or null")
    total_reviews: Optional[int] = Field(default=None, description="Total review count, or null")
    open_now: Optional[bool] = Field(default=None, description="Whether currently open, or null if unknown")
    price_level: Optional[str] = Field(
        default=None, description="e.g. Inexpensive/Moderate/Expensive, or null"
    )
    facilities: list[str] = Field(
        default_factory=list,
        description="Short facility/amenity tags mentioned, e.g. 'outdoor seating', 'wheelchair accessible'",
    )
    menu_summary: Optional[str] = Field(
        default=None, description="General menu/cuisine description, or null if not available"
    )
    google_maps_url: Optional[str] = Field(default=None, description="Direct Google Maps link, or null")


class PlaceDetailsResponse(BaseModel):
    places: list[PlaceDetails] = Field(
        description="One entry per place asked about, in the order the user asked"
    )
