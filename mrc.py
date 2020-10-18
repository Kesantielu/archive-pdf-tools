from os import close, remove

from glob import glob
from tempfile import mkstemp
import subprocess

from PIL import Image
from skimage.filters import threshold_local, threshold_otsu
import numpy as np

import fitz

fitz.TOOLS.set_icc(True) # For good measure, not required



KDU_COMPRESS = '/home/merlijn/archive/microfilm-issue-generator/bin/kdu_compress'
KDU_EXPAND = '/home/merlijn/archive/microfilm-issue-generator/bin/kdu_expand'


def threshold_image(pil_image):
    """
    Apply adaptive (local) thresholding, filtering out background noise to make
    the text more readable. Tesseract uses Otsu thresholding, which in our
    testing hasn't worked all that well, so we perform better thresholding
    before passing the image to tesseract.

    Returns the thresholded PIL image
    """
    img = np.array(pil_image)

    otsu = True
    if otsu:
        binary_otsu = threshold_otsu(img)
        binary_img = img < binary_otsu
    else:
        block_size = 21
        binary_local = threshold_local(img, block_size, method='gaussian')
        #binary_local = threshold_local(img, block_size, offset=10, method='gaussian')
        binary_img = img < binary_local

    return binary_img


#def inverse_mask(mask):
#    inverse_mask = np.copy(mask)
#    inverse_mas[mask == True] = False
#    inverse_mas[mask == False] = True
#

def create_mrc_components(image):
    mask = threshold_image(image)
    #imask = inverse_mask(mask)

    mask_img = Image.fromarray(mask)

    np_im = np.array(image)

    np_bg = np.copy(np_im)
    np_fg = np.copy(np_im)

    # XXX: We likely don't want this to be the 'average'. We might want it (and
    # some neighbouring pixels!) to be 'background' colour, or something like
    # that.
    np_bg[mask] = np.average(np_im)

    # We might not want to touch these pixels, but let's set them to zero for
    # now for good measure.
    # np_fg[mask] = 0

    return mask, np_bg, np_fg


def encode_mrc_images(mask, np_bg, np_fg):
    # Create mask
    #fd, mask_img_png = mkstemp(prefix='mask', suffix='.pgm')
    fd, mask_img_png = mkstemp(prefix='mask', suffix='.png')
    close(fd)
    fd, mask_img_jbig2 = mkstemp(prefix='mask', suffix='.jbig2')
    close(fd)

    img = Image.fromarray(mask)
    img.save(mask_img_png, compress_level=0) # XXX: Check compress_level vs compress

    out = subprocess.check_output(['jbig2', mask_img_png])
    #out = subprocess.check_output(['jbig2', '--pdf', mask_img_png])
    fp= open(mask_img_jbig2, 'wb+')
    fp.write(out)
    fp.close()
    # TODO: re-add this
    #remove(mask_img_png)

    # Create background
    fd, bg_img_tiff = mkstemp(prefix='bg', suffix='.tiff')
    close(fd)
    fd, bg_img_jp2 = mkstemp(prefix='bg', suffix='.jp2')
    close(fd)
    remove(bg_img_jp2) # XXX: Kakadu doesn't want the file to exist, so what are
                       # we even doing

    bg_img = Image.fromarray(np_bg)
    bg_img.save(bg_img_tiff)

    subprocess.check_call([KDU_COMPRESS,
        '-i', bg_img_tiff, '-o', bg_img_jp2,
        '-rate', '0.1',
        # '-roi', '/tmp/image-thres.pgm,0.99',
        ])
    remove(bg_img_tiff)

    # Create foreground
    fd, fg_img_tiff = mkstemp(prefix='fg', suffix='.tiff')
    close(fd)
    fd, fg_img_jp2 = mkstemp(prefix='fg', suffix='.jp2')
    close(fd)
    remove(fg_img_jp2) # XXX: Kakadu doesn't want the file to exist, so what are
                       # we even doing

    fg_img = Image.fromarray(np_fg)
    fg_img.save(fg_img_tiff)

    subprocess.check_call([KDU_COMPRESS,
        '-i', fg_img_tiff, '-o', fg_img_jp2,
        '-rate', '0.05',
        # '-roi', '/tmp/image-thres.pgm,0.99',
        ])
    remove(fg_img_tiff)


    # XXX: Return PNG (which mupdf will turn into ccitt) until mupdf fixes their
    # JBIG2 support
    print(mask_img_png, bg_img_jp2, fg_img_jp2)
    return mask_img_png, bg_img_jp2, fg_img_jp2
    #print(mask_img_jbig2, bg_img_jp2, fg_img_jp2)
    #return mask_img_jbig2, bg_img_jp2, fg_img_jp2


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
            description='PDF recoder using MRC and '\
                        'hOCR for text file placement')
    parser.add_argument('--jp2-stack', help='Base path of unpacked JP2 stack',
                        default=None)
    parser.add_argument('--tesseract-text-only-pdf', help='Path to tesseract'\
                        'text-only PDF (PDF with just invisible text)',
                        default=None)
    parser.add_argument('--out-pdf', help='File to write to', default=None)

    args = parser.parse_args()

    inpath = args.jp2_stack
    tesspath = args.tesseract_text_only_pdf
    outpath = args.out_pdf

    #inpath = '/home/merlijn/archive/tesseract-evaluation/tmp/sim_english-illustrated-magazine_1884-12_2_15_jp2/'

    pdf = fitz.open(tesspath)

    i = 0
    for f in sorted(glob(inpath + '*.jp2')):
        # XXX: in.tiff
        subprocess.check_call([KDU_EXPAND, '-i', f, '-o', '/tmp/in.tiff'])
        mask, bg, fg = create_mrc_components(Image.open('/tmp/in.tiff'))
        mask_f, bg_f, fg_f = encode_mrc_images(mask, bg, fg)

        #page = pdf.newPage(-1)
        page = pdf[i]

        bg_contents = open(bg_f, 'rb').read()
        page.insertImage(page.rect, stream=bg_contents, mask=None)

        fg_contents = open(fg_f, 'rb').read()
        mask_contents = open(mask_f, 'rb').read()

        page.insertImage(page.rect, stream=fg_contents, mask=mask_contents)

        remove(mask_f)
        remove(bg_f)
        remove(fg_f)

        i += 1
        if i % 10 == 0:
            print('Saving')
            pdf.save(outpath)
            #break

    print(fitz.TOOLS.mupdf_warnings())
    pdf.save(outpath, deflate=True)
