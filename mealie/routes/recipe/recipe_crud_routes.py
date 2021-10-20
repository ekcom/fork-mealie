import json
import shutil
from shutil import copyfileobj
from zipfile import ZipFile

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, status
from fastapi.datastructures import UploadFile
from mealie.core.config import settings
from mealie.core.root_logger import get_logger
from mealie.db.database import db
from mealie.db.db_setup import generate_session
from mealie.routes.deps import get_current_user, is_logged_in, temporary_zip_path
from mealie.routes.routers import UserAPIRouter
from mealie.schema.recipe import Recipe, RecipeAsset, RecipeImageTypes, RecipeURLIn
from mealie.schema.user import UserInDB
from mealie.services.events import create_recipe_event
from mealie.services.image.image import scrape_image, write_image
from mealie.services.recipe.media import check_assets, delete_assets
from mealie.services.scraper.scraper import create_from_url, scrape_from_url
from slugify import slugify
from sqlalchemy.orm.session import Session
from starlette.responses import FileResponse

user_router = UserAPIRouter(prefix="/api/recipes", tags=["Recipe CRUD"])
public_router = APIRouter(prefix="/api/recipes", tags=["Recipe CRUD"])
logger = get_logger()


@user_router.post("/create", status_code=201, response_model=str)
def create_from_json(
    background_tasks: BackgroundTasks,
    data: Recipe,
    session: Session = Depends(generate_session),
    current_user=Depends(get_current_user),
) -> str:
    """ Takes in a JSON string and loads data into the database as a new entry"""
    recipe: Recipe = db.recipes.create(session, data.dict())

    background_tasks.add_task(
        create_recipe_event,
        "Recipe Created (URL)",
        f"'{recipe.name}' by {current_user.full_name} \n {settings.BASE_URL}/recipe/{recipe.slug}",
        session=session,
        attachment=recipe.image_dir.joinpath("min-original.webp"),
    )

    return recipe.slug


@user_router.post("/test-scrape-url")
def test_parse_recipe_url(url: RecipeURLIn):
    # Debugger should produce the same result as the scraper sees before cleaning
    scraped_data = scrape_from_url(url.url)
    if scraped_data:
        return scraped_data.schema.data
    return "recipe_scrapers was unable to scrape this URL"


@user_router.post("/create-url", status_code=201, response_model=str)
def parse_recipe_url(
    background_tasks: BackgroundTasks,
    url: RecipeURLIn,
    session: Session = Depends(generate_session),
    current_user: UserInDB = Depends(get_current_user),
):
    """ Takes in a URL and attempts to scrape data and load it into the database """

    recipe = create_from_url(url.url)
    recipe: Recipe = db.recipes.create(session, recipe.dict())

    background_tasks.add_task(
        create_recipe_event,
        "Recipe Created (URL)",
        f"'{recipe.name}' by {current_user.full_name} \n {settings.BASE_URL}/recipe/{recipe.slug}",
        session=session,
        attachment=recipe.image_dir.joinpath("min-original.webp"),
    )

    return recipe.slug


@public_router.get("/{recipe_slug}", response_model=Recipe)
def get_recipe(recipe_slug: str, session: Session = Depends(generate_session), is_user: bool = Depends(is_logged_in)):
    """ Takes in a recipe slug, returns all data for a recipe """

    recipe: Recipe = db.recipes.get(session, recipe_slug)

    if not recipe:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    print(recipe.settings.public, is_user)
    if recipe.settings.public or is_user:

        return recipe

    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN)


@user_router.post("/create-from-zip")
async def create_recipe_from_zip(
    session: Session = Depends(generate_session),
    temp_path=Depends(temporary_zip_path),
    archive: UploadFile = File(...),
):
    """ Create recipe from archive """

    with temp_path.open("wb") as buffer:
        shutil.copyfileobj(archive.file, buffer)

    recipe_dict = None
    recipe_image = None

    with ZipFile(temp_path) as myzip:
        for file in myzip.namelist():
            if file.endswith(".json"):
                with myzip.open(file) as myfile:
                    recipe_dict = json.loads(myfile.read())
            elif file.endswith(".webp"):
                with myzip.open(file) as myfile:
                    recipe_image = myfile.read()

    recipe: Recipe = db.recipes.create(session, Recipe(**recipe_dict))

    write_image(recipe.slug, recipe_image, "webp")

    return recipe


@public_router.get("/{recipe_slug}/zip")
async def get_recipe_as_zip(
    recipe_slug: str, session: Session = Depends(generate_session), temp_path=Depends(temporary_zip_path)
):
    """ Get a Recipe and It's Original Image as a Zip File """
    recipe: Recipe = db.recipes.get(session, recipe_slug)

    image_asset = recipe.image_dir.joinpath(RecipeImageTypes.original.value)

    with ZipFile(temp_path, "w") as myzip:
        myzip.writestr(f"{recipe_slug}.json", recipe.json())

        if image_asset.is_file():
            myzip.write(image_asset, arcname=image_asset.name)

    return FileResponse(temp_path, filename=f"{recipe_slug}.zip")


@user_router.put("/{recipe_slug}")
def update_recipe(
    recipe_slug: str,
    data: Recipe,
    session: Session = Depends(generate_session),
):
    """ Updates a recipe by existing slug and data. """

    recipe: Recipe = db.recipes.update(session, recipe_slug, data.dict())

    check_assets(original_slug=recipe_slug, recipe=recipe)

    return recipe


@user_router.patch("/{recipe_slug}")
def patch_recipe(
    recipe_slug: str,
    data: Recipe,
    session: Session = Depends(generate_session),
):
    """ Updates a recipe by existing slug and data. """

    recipe: Recipe = db.recipes.patch(
        session, recipe_slug, new_data=data.dict(exclude_unset=True, exclude_defaults=True)
    )

    check_assets(original_slug=recipe_slug, recipe=recipe)

    return recipe


@user_router.delete("/{recipe_slug}")
def delete_recipe(
    background_tasks: BackgroundTasks,
    recipe_slug: str,
    session: Session = Depends(generate_session),
    current_user=Depends(get_current_user),
):
    """ Deletes a recipe by slug """

    try:
        recipe: Recipe = db.recipes.delete(session, recipe_slug)
        delete_assets(recipe_slug=recipe_slug)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)

    background_tasks.add_task(
        create_recipe_event, "Recipe Deleted", f"'{recipe.name}' deleted by {current_user.full_name}", session=session
    )

    return recipe


@user_router.put("/{recipe_slug}/image")
def update_recipe_image(
    recipe_slug: str,
    image: bytes = File(...),
    extension: str = Form(...),
    session: Session = Depends(generate_session),
):
    """ Removes an existing image and replaces it with the incoming file. """
    write_image(recipe_slug, image, extension)
    new_version = db.recipes.update_image(session, recipe_slug, extension)

    return {"image": new_version}


@user_router.post("/{recipe_slug}/image")
def scrape_image_url(
    recipe_slug: str,
    url: RecipeURLIn,
):
    """ Removes an existing image and replaces it with the incoming file. """

    scrape_image(url.url, recipe_slug)


@user_router.post("/{recipe_slug}/assets", response_model=RecipeAsset)
def upload_recipe_asset(
    recipe_slug: str,
    name: str = Form(...),
    icon: str = Form(...),
    extension: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(generate_session),
):
    """ Upload a file to store as a recipe asset """
    file_name = slugify(name) + "." + extension
    asset_in = RecipeAsset(name=name, icon=icon, file_name=file_name)
    dest = Recipe(slug=recipe_slug).asset_dir.joinpath(file_name)

    with dest.open("wb") as buffer:
        copyfileobj(file.file, buffer)

    if not dest.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)

    recipe: Recipe = db.recipes.get(session, recipe_slug)
    recipe.assets.append(asset_in)
    db.recipes.update(session, recipe_slug, recipe.dict())
    return asset_in
